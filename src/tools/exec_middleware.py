# -*- coding: utf-8 -*-
# src/tools/exec_middleware.py
# Middleware global (todas las estrategias): reglas exchange + cap 15% + fees + slippage + latencias

import backtrader as bt
from tools.exchange_rules import bt_conform_market_order
from tools.fees import fee_amount
from tools.slippage import default_slippage_cfg, default_latency_cfg, apply_slippage


def enable_exec_middleware(cerebro: bt.Cerebro, ex_rules, fees_cfg,
                           max_alloc_pct=0.85,
                           slip_cfg=None, lat_cfg=None):
    """
    Actívalo una vez en el runner. Aplica a TODAS las estrategias.
    - Ajusta qty a tick/step y minNotional; respeta cap 15%.
    - Aplica slippage determinista (spread + vol + tamaño + latencia).
    - Calcula fee de entrada/salida (taker por defecto).
    """

    slip_cfg = slip_cfg or default_slippage_cfg()
    lat_cfg = lat_cfg or default_latency_cfg()

    orig_buy = bt.Strategy.buy
    orig_sell = bt.Strategy.sell
    orig_close = bt.Strategy.close

    def _symbol(self, data):
        return getattr(data, '_name', None) or getattr(self.params, 'symbol_name', None)

    # ---------- BUY wrapper (MARKET) ----------
    def buy_wrapper(self: bt.Strategy, *args, **kwargs):
        data = kwargs.get('data', self.datas[0])
        size = kwargs.get('size', args[0] if args else 0.0)
        if not size:
            return None

        px_ref = float(data.close[0])
        sym = _symbol(self, data)
        rules = (ex_rules or {}).get(sym, {})
        cap_new = float(self.broker.getvalue()) * float(max_alloc_pct)

        # 1) Reglas del exchange + cap
        ok, qty_adj, notional_adj, reason = bt_conform_market_order(
            rules=rules,
            side="buy",
            ref_price=px_ref,
            qty=float(size),
            cap_notional=cap_new
        )
        if not ok or qty_adj <= 0:
            print(f"[EXEC-MW][SKIP] {sym} {self.datetime.datetime(0)} motivo={reason} cap={cap_new:.2f}")
            return None

        # 2) Slippage (precio efectivo de compra)
        px_eff = apply_slippage("buy", px_ref, qty_adj, data, lat_cfg, slip_cfg)
        notional_eff = px_eff * qty_adj

        # 3) Fee de entrada y chequeo de cash/cap
        fee_in = fee_amount(notional_eff, 'taker', fees_cfg or {})
        cash = float(self.broker.get_cash())
        if notional_eff + fee_in > cash + 1e-6:
            print(f"[EXEC-MW][SKIP] {sym} cash insuficiente (notional+fee>{cash:.2f})")
            return None
        if notional_eff > cap_new + 1e-6:
            print(f"[EXEC-MW][SKIP] {sym} excede cap tras slippage ({notional_eff:.2f}>{cap_new:.2f})")
            return None

        # 4) Pasar size ajustado
        kwargs['size'] = qty_adj
        order = orig_buy(self, *args, **kwargs)

        # 5) Adjuntar info para el analyzer (precio efectivo y fee_in)
        if order is not None:
            try:
                order.addinfo(
                    _px_eff_in=px_eff,
                    _entry_notional=notional_eff,
                    _fee_in=fee_in,
                    _symbol=sym
                )
            except Exception:
                pass

        return order

    # ---------- SELL wrapper (MARKET) ----------
    def sell_wrapper(self: bt.Strategy, *args, **kwargs):
        data = kwargs.get('data', self.datas[0])
        size = kwargs.get('size', args[0] if args else 0.0)

        # Si la estrategia no pasa size, cerramos la pos actual
        if not size or size <= 0.0:
            pos = self.getposition(data)
            size = abs(float(pos.size))
            if size <= 0:
                return None
            kwargs['size'] = size

        px_ref = float(data.close[0])
        sym = _symbol(self, data)

        # Precio efectivo de venta con slippage
        px_eff = apply_slippage("sell", px_ref, float(size), data, lat_cfg, slip_cfg)
        notional_eff = px_eff * float(size)

        order = orig_sell(self, *args, **kwargs)

        if order is not None:
            try:
                order.addinfo(
                    _px_eff_out=px_eff,
                    _exit_notional=notional_eff,
                    _symbol=sym
                )
            except Exception:
                pass

        return order

    # ---------- CLOSE wrapper ----------
    def close_wrapper(self: bt.Strategy, *args, **kwargs):
        data = kwargs.get('data', self.datas[0])
        pos = self.getposition(data)
        size = abs(float(pos.size))
        if size <= 0.0:
            return None

        return sell_wrapper(
            self,
            size=size,
            data=data,
            **{k: v for k, v in kwargs.items() if k not in ('size', 'data')}
        )

    # Parcheos globales
    bt.Strategy.buy = buy_wrapper
    bt.Strategy.sell = sell_wrapper
    bt.Strategy.close = close_wrapper


# ======================================================
#             ANALYZER GLOBAL (PnL NETO)
# ======================================================

class FeesNetAnalyzer(bt.Analyzer):
    """
    Analyzer global: usa precios EFECTIVOS con slippage (si existen en order.info)
    + fees de entrada/salida → PnL neto por trade.
    """

    params = (('fees_cfg', None),)

    def start(self):
        self.rows = []
        self._open = {}

    def notify_order(self, order):
        if order.status != order.Completed:
            return

        data = order.data
        sym = getattr(order.info, '_symbol', getattr(data, '_name', None))
        dt = self.strategy.datetime.datetime(0)
        size = abs(float(order.executed.size))
        if size <= 0:
            return

        px_exec = float(order.executed.price)
        px_in_eff = float(getattr(order.info, '_px_eff_in', 0.0))
        px_out_eff = float(getattr(order.info, '_px_eff_out', 0.0))

        if order.isbuy():
            px = px_in_eff if px_in_eff > 0 else px_exec
            notional = px * size
            fee_in = float(getattr(order.info, '_fee_in', 0.0))

            b = self._open.setdefault(
                sym,
                {
                    "entry_notional": 0.0,
                    "fee_in": 0.0,
                    "qty": 0.0,
                    "px_vwap": 0.0,
                    "dt": dt
                }
            )
            b["entry_notional"] += notional
            b["fee_in"] += fee_in
            b["qty"] += size
            if b["qty"] > 0:
                b["px_vwap"] = b["entry_notional"] / b["qty"]

        elif order.issell():
            from tools.fees import fee_amount
            px = px_out_eff if px_out_eff > 0 else px_exec
            notional = px * size
            fee_out = fee_amount(notional, 'taker', self.params.fees_cfg or {})

            pos = self.strategy.getposition(data)

            # si la posición queda a 0 → cerrar trade
            if pos.size == 0 and sym in self._open:
                b = self._open.pop(sym)

                px_in = b["px_vwap"]
                qty = b["qty"]
                fee_in = b["fee_in"]

                pnl_bruto = (px - px_in) * qty
                pnl_neto = pnl_bruto - fee_in - fee_out

                self.rows.append({
                    "Símbolo": sym,
                    "Fecha entrada": b["dt"],
                    "Precio entrada (eff)": round(px_in, 8),
                    "Tamaño": round(qty, 8),
                    "Fee entrada (€)": round(fee_in, 2),
                    "Fecha salida": dt,
                    "Precio salida (eff)": round(px, 8),
                    "Fee salida (€)": round(fee_out, 2),
                    "PnL bruto (€)": round(pnl_bruto, 2),
                    "PnL neto (€)": round(pnl_neto, 2)
                })

    def get_analysis(self):
        return self.rows
