# -*- coding: utf-8 -*-
# src/tools/exec_middleware.py
# Middleware global (todas las estrategias): reglas exchange + fees + slippage + latencias
#
# Versión Pablo:
# - Ajusta qty a tick/step y minNotional (via exchange_rules).
# - Aplica slippage determinista y latencia.
# - Aplica fees tipo taker.
# - Ajusta el tamaño si notional+fee excede el cash disponible (en vez de cancelar).
# - NO impone ya el antiguo cap del 15% del portfolio.
# - cap_notional interno de exchange_rules se desactiva (0.0).

import backtrader as bt
from tools.exchange_rules import bt_conform_market_order
from tools.fees import fee_amount
from tools.slippage import default_slippage_cfg, default_latency_cfg, apply_slippage


def enable_exec_middleware(cerebro: bt.Cerebro, ex_rules, fees_cfg,
                           max_alloc_pct=1.0,
                           slip_cfg=None, lat_cfg=None):
    """
    Actívalo una vez en el runner. Aplica a TODAS las estrategias.

    Parámetros:
        cerebro      : instancia de bt.Cerebro.
        ex_rules     : dict con reglas por símbolo (tickSize, stepSize, minNotional, etc.).
        fees_cfg     : config de fees (p.ej. salida de default_fees_cfg()).
        max_alloc_pct: (no se usa en esta versión, mantenido por compatibilidad).
        slip_cfg     : config de slippage (si None, usa default_slippage_cfg()).
        lat_cfg      : config de latencia (si None, usa default_latency_cfg()).

    Efectos:
        - Se parchean bt.Strategy.buy/sell/close para que:
          * Ajusten size según exchange_rules.
          * Apliquen slippage/latencia.
          * Calculen fees de entrada/salida.
          * No gasten más cash del disponible (escalando tamaño si hace falta).
    """

    slip_cfg = slip_cfg or default_slippage_cfg()
    lat_cfg = lat_cfg or default_latency_cfg()

    # Guardamos los métodos originales
    orig_buy = bt.Strategy.buy
    orig_sell = bt.Strategy.sell
    orig_close = bt.Strategy.close

    def _symbol(self, data):
        return getattr(data, '_name', None) or getattr(self.params, 'symbol_name', None)

    # ---------- BUY wrapper (MARKET) ----------
    def buy_wrapper(self: bt.Strategy, *args, **kwargs):
        data = kwargs.get('data', self.datas[0])

        # backtrader permite pasar size como primer arg posicional o como kwarg
        if 'size' in kwargs:
            size = kwargs['size']
        elif args:
            size = args[0]
        else:
            size = 0.0

        size = float(size)
        if not size or size <= 0.0:
            return None

        px_ref = float(data.close[0])
        if px_ref <= 0:
            return None

        sym = _symbol(self, data)
        rules = (ex_rules or {}).get(sym, {})

        # cap_notional = 0 → NO aplicamos cap interno en exchange_rules
        cap_new = 0.0

        # 1) Ajuste inicial por reglas del exchange (tick/step/minNotional)
        ok, qty_adj, notional_adj, reason = bt_conform_market_order(
            rules=rules,
            side="buy",
            ref_price=px_ref,
            qty=size,
            cap_notional=cap_new
        )

        if (not ok) or qty_adj <= 0:
            print(f"[EXEC-MW][SKIP] {sym} {self.datetime.datetime(0)} motivo={reason} cap={cap_new:.2f}")
            return None

        cash = float(self.broker.get_cash())

        # 2) Slippage inicial y cálculo de coste
        px_eff = apply_slippage("buy", px_ref, qty_adj, data, lat_cfg, slip_cfg)
        notional_eff = px_eff * qty_adj
        fee_in = fee_amount(notional_eff, 'taker', fees_cfg or {})
        total_cost = notional_eff + fee_in

        # 3) Si el coste total excede el cash, ESCALAMOS el size en vez de cancelar
        if total_cost > cash + 1e-6:
            if total_cost <= 0:
                print(f"[EXEC-MW][SKIP] {sym} {self.datetime.datetime(0)} motivo=total_cost<=0")
                return None

            scale = cash / total_cost
            if scale <= 0:
                print(f"[EXEC-MW][SKIP] {sym} {self.datetime.datetime(0)} motivo=scale<=0")
                return None

            qty_scaled = qty_adj * scale

            # Volvemos a aplicar exchange_rules por si al escalar rompemos minNotional/step
            ok2, qty_adj2, notional_adj2, reason2 = bt_conform_market_order(
                rules=rules,
                side="buy",
                ref_price=px_ref,
                qty=qty_scaled,
                cap_notional=0.0
            )
            if (not ok2) or qty_adj2 <= 0:
                print(f"[EXEC-MW][SKIP] {sym} {self.datetime.datetime(0)} motivo={reason2} tras escalar")
                return None

            qty_adj = qty_adj2
            px_eff = apply_slippage("buy", px_ref, qty_adj, data, lat_cfg, slip_cfg)
            notional_eff = px_eff * qty_adj
            fee_in = fee_amount(notional_eff, 'taker', fees_cfg or {})
            total_cost = notional_eff + fee_in

            if total_cost > cash + 1e-6:
                # Si aún así, por redondeos, sigue pasando, ya sí la descartamos
                print(
                    f"[EXEC-MW][SKIP] {sym} {self.datetime.datetime(0)} "
                    f"motivo=cash_insuficiente_tras_escalar (total_cost>{cash:.2f})"
                )
                return None

        # 4) Pasar size ajustado al método original
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

        # backtrader permite pasar size como primer arg posicional o como kwarg
        if 'size' in kwargs:
            size = kwargs['size']
        elif args:
            size = args[0]
        else:
            size = 0.0

        size = float(size)

        # Si la estrategia no pasa size o pasa size <= 0, cerramos la posición actual
        if not size or size <= 0.0:
            pos = self.getposition(data)
            size = abs(float(pos.size))
            if size <= 0:
                return None
            kwargs['size'] = size

        px_ref = float(data.close[0])
        if px_ref <= 0:
            return None

        sym = _symbol(self, data)

        # Precio efectivo de venta con slippage
        px_eff = apply_slippage("sell", px_ref, size, data, lat_cfg, slip_cfg)
        notional_eff = px_eff * size

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

        # Reutilizamos sell_wrapper para que aplique slippage + fees de salida
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
    Además acumula:
      - total_fee_in  : suma de todas las fees de entrada
      - total_fee_out : suma de todas las fees de salida
    """

    params = (('fees_cfg', None),)

    def start(self):
        self.rows = []
        self._open = {}
        self.total_fee_in = 0.0
        self.total_fee_out = 0.0

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

            # acumulamos fee de entrada global
            self.total_fee_in += fee_in

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

            # acumulamos fee de salida global
            self.total_fee_out += fee_out

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
