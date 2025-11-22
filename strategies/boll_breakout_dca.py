import backtrader as bt

class Strategy(bt.Strategy):
    params = dict(
        monthly_budget=300.0,   # euros que se añaden cada mes
        bb_period=20,
        bb_dev=2.0,
        squeeze_threshold=0.12,
        vol_period=20,
        trend_filter=True,
        body_ratio_min=0.5,
    )

    def __init__(self):
        # Indicadores
        self.ema20 = bt.ind.EMA(self.data.close, period=20)
        self.ema50 = bt.ind.EMA(self.data.close, period=50)
        self.atr = bt.ind.ATR(self.data, period=14)

        self.bb = bt.ind.BollingerBands(
            self.data.close,
            period=self.p.bb_period,
            devfactor=self.p.bb_dev
        )
        self.bb_width = (self.bb.top - self.bb.bot) / (self.data.close + 1e-8)
        self.vol_ma = bt.ind.SMA(self.data.volume, period=self.p.vol_period)

        # Variables nuevas
        self.current_month = None
        self.acc_budget = 0.0       # presupuesto acumulado
        self.bought_this_month = False

        self.trade_log = []

    def is_breakout_candle(self):
        o = float(self.data.open[0])
        c = float(self.data.close[0])
        h = float(self.data.high[0])
        l = float(self.data.low[0])

        body = abs(c - o)
        range_ = max(h - l, 1e-8)
        body_ratio = body / range_

        return body_ratio >= self.p.body_ratio_min and c > o

    def next(self):
        dt = self.data.datetime.datetime()
        close = float(self.data.close[0])
        bb_w = float(self.bb_width[0])

        # Detectar cambio de mes
        if self.current_month != dt.month:
            self.current_month = dt.month
            self.bought_this_month = False
            self.acc_budget += self.p.monthly_budget   # añadir presupuesto del mes nuevo

        # Si ya hemos comprado este mes, no hacer más
        if self.bought_this_month:
            return

        # Señales del boll breakout
        cond_squeeze = bb_w <= self.p.squeeze_threshold
        cond_breakout = close > float(self.bb.top[0]) and self.is_breakout_candle()
        cond_vol = float(self.data.volume[0]) > float(self.vol_ma[0])
        cond_trend = float(self.ema20[0]) > float(self.ema50[0]) if self.p.trend_filter else True

        if cond_squeeze and cond_breakout and cond_vol and cond_trend:
            if self.acc_budget <= 0:
                return

            amount = min(self.acc_budget, float(self.broker.getcash()))
            if amount <= 0:
                return

            size = amount / close
            self.buy(size=size)

            self.trade_log.append({
                "dt": dt,
                "type": "BUY",
                "price": close,
                "size": size,
                "amount_invested": amount,
                "acc_budget_before": self.acc_budget,
            })

            self.acc_budget = 0.0          # limpiar presupuesto tras invertir
            self.bought_this_month = True   # bloquear compras este mes
