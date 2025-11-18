import backtrader as bt


class Strategy(bt.Strategy):
    params = dict(
        # --- Parámetros de la estrategia ---
        risk_pct=0.01,        # 1% del equity en riesgo por operación
        atr_mult=2.0,         # stop = close - 2*ATR
        bb_period=20,
        bb_dev=2.0,
        squeeze_threshold=0.12,  # banda superior - inferior / close
        vol_period=20,
        trend_filter=True,    # require EMA20 > EMA50
        body_ratio_min=0.5,   # vela breakout debe tener cuerpo >= 50% del rango

        # --- Necesarios para bt-lab ---
        max_alloc_pct=None,
        onramp_max=None,
        onramp_risk_cap=None,
    )

    def __init__(self):
        # Indicadores de tendencia
        self.ema20 = bt.ind.EMA(self.data.close, period=20)
        self.ema50 = bt.ind.EMA(self.data.close, period=50)

        # ATR
        self.atr = bt.ind.ATR(self.data, period=14)

        # Bollinger Bands
        self.bb = bt.ind.BollingerBands(
            self.data.close,
            period=self.p.bb_period,
            devfactor=self.p.bb_dev
        )
        self.bb_width = (self.bb.lines.top - self.bb.lines.bot) / (self.data.close + 1e-8)

        # Volumen
        self.vol_ma = bt.ind.SMA(self.data.volume, period=self.p.vol_period)

        # Para guardar logs compatibles con bt-lab
        self.trade_log = []
        self.stop_price = None

    def is_breakout_candle(self):
        """Detecta si la vela actual es breakout con cuerpo grande."""
        o = float(self.data.open[0])
        c = float(self.data.close[0])
        h = float(self.data.high[0])
        l = float(self.data.low[0])

        body = abs(c - o)
        range_ = max(h - l, 1e-8)

        body_ratio = body / range_

        return body_ratio >= self.p.body_ratio_min and c > o

    def next(self):
        close = float(self.data.close[0])
        atr = float(self.atr[0])
        bb_w = float(self.bb_width[0])

        if atr <= 0:
            return

        # ==========================
        #    SIN POSICIÓN → ENTRADA
        # ==========================
        if not self.position:
            cond_squeeze = bb_w <= self.p.squeeze_threshold
            cond_breakout = close > float(self.bb.lines.top[0]) and self.is_breakout_candle()
            cond_vol = float(self.data.volume[0]) > float(self.vol_ma[0])

            cond_trend = True
            if self.p.trend_filter:
                cond_trend = float(self.ema20[0]) > float(self.ema50[0])

            if cond_squeeze and cond_breakout and cond_vol and cond_trend:
                equity = float(self.broker.getvalue())
                risk_amount = equity * self.p.risk_pct
                risk_per_unit = self.p.atr_mult * atr
                size = risk_amount / risk_per_unit

                if size <= 0:
                    return

                # stop dinámico
                self.stop_price = close - self.p.atr_mult * atr

                self.buy(size=size)

                self.trade_log.append({
                    "dt": self.data.datetime.datetime(),
                    "type": "BUY",
                    "price": close,
                    "size": size,
                    "atr": atr,
                    "bb_width": bb_w,
                    "volume": float(self.data.volume[0]),
                    "vol_ma": float(self.vol_ma[0]),
                })
                return

        # ==========================
        #    CON POSICIÓN → SALIDA
        # ==========================
        else:
            # STOP ATR
            if self.stop_price and close <= self.stop_price:
                self.close()
                self.trade_log.append({
                    "dt": self.data.datetime.datetime(),
                    "type": "STOP",
                    "price": close,
                })
                self.stop_price = None
                return

            # Salida por debilidad: cierre bajo EMA20
            if close < float(self.ema20[0]):
                self.close()
                self.trade_log.append({
                    "dt": self.data.datetime.datetime(),
                    "type": "EXIT",
                    "price": close,
                })
                self.stop_price = None
                return
