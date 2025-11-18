import backtrader as bt


class Strategy(bt.Strategy):
    params = dict(
        risk_pct=0.01,
        atr_mult=2.0,
        strength_entry=1.0,
        strength_exit=0.0,
        rsi_lower=50,
        rsi_upper=70,

        # Necesarios para bt-lab (aunque no se usen)
        max_alloc_pct=None,
        onramp_max=None,
        onramp_risk_cap=None,
    )

    def __init__(self):
        self.ema20 = bt.ind.EMA(self.data.close, period=20)
        self.ema50 = bt.ind.EMA(self.data.close, period=50)
        self.atr = bt.ind.ATR(self.data, period=14)
        self.rsi = bt.ind.RSI(self.data.close, period=14)

        self.trend_strength = (self.ema20 - self.ema50) / (self.atr + 1e-8)

        self.trade_log = []
        self.stop_price = None

    def next(self):
        close = float(self.data.close[0])
        atr = float(self.atr[0])
        rsi = float(self.rsi[0])
        strength = float(self.trend_strength[0])
        equity = float(self.broker.getvalue())

        if atr <= 0:
            return

        # =======================
        # ENTRADA (solo long)
        # =======================
        if not self.position:
            if (
                strength > self.p.strength_entry and
                close > self.ema50[0] and
                self.p.rsi_lower <= rsi <= self.p.rsi_upper
            ):
                risk_per_unit = self.p.atr_mult * atr
                risk_amount = equity * self.p.risk_pct
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
                    "strength": strength,
                    "atr": atr,
                })

        # =======================
        # SALIDA
        # =======================
        else:
            # stop dinámico
            if self.stop_price and close <= self.stop_price:
                self.close()
                self.trade_log.append({
                    "dt": self.data.datetime.datetime(),
                    "type": "STOP",
                    "price": close,
                })
                self.stop_price = None
                return

            # pérdida de fuerza
            if strength < self.p.strength_exit or close < self.ema50[0]:
                self.close()
                self.trade_log.append({
                    "dt": self.data.datetime.datetime(),
                    "type": "EXIT",
                    "price": close,
                })
                self.stop_price = None
