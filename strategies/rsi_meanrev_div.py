import backtrader as bt


class Strategy(bt.Strategy):
    params = dict(
        risk_pct=0.01,
        atr_mult=2.0,
        rsi_oversold=30,
        rsi_overbought=70,
        div_lookback=10,
        ema_fast_period=5,
        ema_slow_period=20,

        max_alloc_pct=None,
        onramp_max=None,
        onramp_risk_cap=None,
    )

    def __init__(self):
        self.rsi = bt.ind.RSI(self.data.close, period=14)
        self.atr = bt.ind.ATR(self.data, period=14)
        self.ema_fast = bt.ind.EMA(self.data.close, period=self.p.ema_fast_period)
        self.ema_slow = bt.ind.EMA(self.data.close, period=self.p.ema_slow_period)

        self.trade_log = []
        self.stop_price = None

        # 🔥 Nuevo: recordamos cuándo hubo sobreventa
        self.last_oversold_idx = None


    def _divergence_happened(self):
        """Divergencia dentro de las últimas N velas (no en la vela actual)."""
        L = self.p.div_lookback
        if len(self.data) < L + 3:
            return False

        lows = [float(self.data.low[-i]) for i in range(1, L + 1)]
        rsis = [float(self.rsi[-i]) for i in range(1, L + 1)]

        price_now = float(self.data.low[0])
        rsi_now = float(self.rsi[0])

        price_new_low = price_now <= min(lows)
        rsi_not_new_low = rsi_now > min(rsis)
        rsi_turning = rsi_now > float(self.rsi[-1])

        return price_new_low and rsi_not_new_low and rsi_turning


    def next(self):
        close = float(self.data.close[0])
        open_ = float(self.data.open[0])
        atr = float(self.atr[0])
        rsi_val = float(self.rsi[0])

        if atr <= 0:
            return

        # ==================================================
        # 1) Registrar sobreventa
        # ==================================================
        if rsi_val < self.p.rsi_oversold:
            self.last_oversold_idx = len(self.data)  # posición actual

        # Si nunca hubo sobreventa reciente → no seguimos
        if self.last_oversold_idx is None:
            return

        # Si pasaron más de X velas desde la sobreventa → reset
        if len(self.data) - self.last_oversold_idx > 20:
            self.last_oversold_idx = None
            return

        # ==================================================
        # 2) Chequear divergencia
        # ==================================================
        if not self._divergence_happened():
            return

        # ==================================================
        # 3) Necesitamos confirmación ahora
        # ==================================================
        cond_rebound = (
            float(self.ema_fast[0]) > float(self.ema_slow[0]) and
            close > open_
        )

        if not cond_rebound:
            return

        # ==================================================
        # 4) ENTRADA
        # ==================================================
        if not self.position:
            equity = float(self.broker.getvalue())
            risk_amount = equity * self.p.risk_pct
            risk_per_unit = self.p.atr_mult * atr
            size = risk_amount / risk_per_unit

            if size <= 0:
                return

            self.stop_price = close - self.p.atr_mult * atr
            self.buy(size=size)

            self.trade_log.append({
                "dt": self.data.datetime.datetime(),
                "type": "BUY",
                "price": close,
                "size": size,
                "rsi": rsi_val,
            })

            # reset del trigger
            self.last_oversold_idx = None
            return

        # ==================================================
        # 5) SALIDAS
        # ==================================================
        if self.position:
            # Stop
            if self.stop_price and close <= self.stop_price:
                self.close()
                self.stop_price = None
                self.trade_log.append({
                    "dt": self.data.datetime.datetime(),
                    "type": "STOP",
                    "price": close,
                })
                return

            # RSI salida o momentum perdido
            cond_exit = (
                rsi_val > self.p.rsi_overbought or
                close < float(self.ema_fast[0])
            )

            if cond_exit:
                self.close()
                self.stop_price = None
                self.trade_log.append({
                    "dt": self.data.datetime.datetime(),
                    "type": "EXIT",
                    "price": close,
                })
                return
