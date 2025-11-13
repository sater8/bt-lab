# -*- coding: utf-8 -*-
# src/tools/exec_middleware.py
# Middleware global: reglas de exchange + cap 15% + fees (taker) en TODAS las estrategias
import backtrader as bt
from tools.exchange_rules import bt_conform_market_order
from tools.fees import fee_amount

def enable_exec_middleware(cerebro: bt.Cerebro, ex_rules, fees_cfg, max_alloc_pct=0.85):
    orig_buy = bt.Strategy.buy
    def buy_wrapper(self, *args, **kwargs):
        data = kwargs.get('data', self.datas[0])
        size = kwargs.get('size', args[0] if args else 0.0)
        if not size: return None
        px = float(data.close[0])
        sym = getattr(data, '_name', None) or getattr(self.params, 'symbol_name', None)
        rules = (ex_rules or {}).get(sym, {})
        cap_new = float(self.broker.getvalue()) * float(max_alloc_pct)
        ok, qty_adj, notion_adj, reason = bt_conform_market_order(
            rules=rules, side="buy", ref_price=px, qty=float(size), cap_notional=cap_new
        )
        if not ok or qty_adj <= 0:
            print(f"[EXEC-MW][SKIP] {sym} {self.datetime.datetime(0)} motivo={reason} cap={cap_new:.2f}")
            return None
        kwargs['size'] = qty_adj
        order = orig_buy(self, *args, **kwargs)
        if order is not None:
            fee_in = fee_amount(notion_adj, 'taker', fees_cfg or {})
            try: order.addinfo(_fee_in=fee_in, _entry_notional=notion_adj, _symbol=sym)
            except Exception: pass
        return order
    bt.Strategy.buy = buy_wrapper

class FeesNetAnalyzer(bt.Analyzer):
    params = (('fees_cfg', None),)
    def start(self): self.rows=[]; self._open={}
    def notify_order(self, order):
        if order.status != order.Completed: return
        data=order.data; sym=getattr(data,'_name',None); dt=self.strategy.datetime.datetime(0)
        price=float(order.executed.price); size=abs(float(order.executed.size)); notion=price*size
        if order.isbuy():
            fee_in=float(getattr(order.info,'_fee_in',0.0)); en=float(getattr(order.info,'_entry_notional',notion))
            b=self._open.setdefault(sym,{"entry_notional":0.0,"fee_in":0.0,"qty":0.0,"px_vwap":0.0,"dt":dt})
            b["entry_notional"]+=en; b["fee_in"]+=fee_in; b["qty"]+=size
            if b["qty"]>0: b["px_vwap"]=b["entry_notional"]/b["qty"]
        elif order.issell():
            from tools.fees import fee_amount
            fee_out=fee_amount(notion,'taker',self.params.fees_cfg or {})
            pos=self.strategy.getposition(data)
            if pos.size==0 and sym in self._open:
                b=self._open.pop(sym); px_in=b["px_vwap"]; qty=b["qty"]; fee_in=b["fee_in"]
                pnl_bruto=(price-px_in)*qty; pnl_neto=pnl_bruto-fee_in-fee_out
                self.rows.append({
                    "Símbolo": sym, "Fecha entrada": b["dt"], "Precio entrada": round(px_in,8),
                    "Tamaño": round(qty,8), "Fee entrada (€)": round(fee_in,2),
                    "Fecha salida": dt, "Precio salida": round(price,8),
                    "Fee salida (€)": round(fee_out,2),
                    "PnL bruto (€)": round(pnl_bruto,2), "PnL neto (€)": round(pnl_neto,2)
                })
    def get_analysis(self): return self.rows