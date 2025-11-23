# src/tools/sizing.py
import backtrader as bt

class PabloSizingMixin(bt.Strategy):
    """
    Mixin para estrategias que quieran un sistema de sizing estándar:
    - all_in: usa todo el cash disponible en cada entrada
    - fixed: usa una cantidad fija de cash (p.ej. 300)
    - percent: usa un % del cash disponible (0.25 = 25%)
    """

    params = dict(
        sizing_mode="all_in",  # "all_in", "fixed", "percent"
        fixed_stake=0.0,       # en moneda (ej. 300€)
        stake_pct=1.0          # 0–1, porcentaje del cash disponible
    )

    def _compute_stake_cash(self):
        cash = self.broker.getcash()

        if self.p.sizing_mode == "all_in":
            # Dejamos un pequeño margen para slippage + comisiones,
            # para que la orden no falle por "cash insuficiente".
            stake_cash = cash * 0.995   # usa aprox. el 99.5% del cash
        elif self.p.sizing_mode == "fixed":
            stake_cash = min(self.p.fixed_stake, cash)
        elif self.p.sizing_mode == "percent":
            stake_cash = cash * max(0.0, min(self.p.stake_pct, 1.0))
        else:
            stake_cash = cash

        return max(stake_cash, 0.0)

    def get_stake_size(self, price: float) -> float:
        """
        Devuelve el size en unidades de asset, ya ajustado para no
        gastar más cash del disponible. El ajuste fino a step/tick
        lo hará el exec_middleware.
        """
        if price <= 0:
            return 0.0

        stake_cash = self._compute_stake_cash()

        # Size "ideal" en unidades de asset
        size = stake_cash / price

        return max(size, 0.0)
