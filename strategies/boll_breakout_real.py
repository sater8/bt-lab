import backtrader as bt


class Strategy(bt.Strategy):
    params = dict(
        # --- Parámetros de la estrategia (idénticos al bot) ---
        risk_pct=0.01,        # 1% del equity en riesgo teórico
        atr_mult=2.0,         # stop teórico = close - 2*ATR
        bb_period=20,
        bb_dev=2.0,
        squeeze_threshold=0.12,  # banda superior - inferior / close
        vol_period=20,
        trend_filter=True,    # require EMA20 > EMA50
        body_ratio_min=0.5,   # vela breakout debe tener cuerpo >= 50% del rango

        # --- Necesarios para bt-lab (aunque aquí no los usemos explícitamente) ---
        max_alloc_pct=None,
        onramp_max=None,
        onramp_risk_cap=None,
    )

    def __init__(self):
        # Indicadores de tendencia
        self.ema20 = bt.ind.EMA(self.data.close, period=20)
        self.ema50 = bt.ind.EMA(self.data.close, period=50)

        # ATR (para sizing e invest_pct, igual que el bot)
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
        self.stop_price = None  # solo informativo, como en el bot

    def is_breakout_candle(self):
        """Misma lógica que is_breakout_candle(row) del bot."""
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

                # Igual que el bot: riesgo teórico = 1% del equity
                risk_amount = equity * self.p.risk_pct
                risk_per_unit = self.p.atr_mult * atr

                if risk_per_unit <= 0:
                    return

                size = risk_amount / risk_per_unit
                if size <= 0:
                    return

                # stop "teórico", como en el bot (pero NO lo usamos para salir)
                self.stop_price = close - self.p.atr_mult * atr

                # invest_pct igual que en el bot (solo informativo)
                invest_pct = (self.p.risk_pct * close / (self.p.atr_mult * atr)) * 100.0

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
                    "equity": equity,
                    "invest_pct": invest_pct,
                    "stop_price_theoretical": self.stop_price,
                })
                return

        # ==========================
        #    CON POSICIÓN → SALIDA
        # ==========================
        else:
            # 👇 MUY IMPORTANTE:
            # El bot SOLO sale cuando el cierre cae por debajo de EMA20.
            # No usa nunca el stop ATR en su lógica real.
            if close < float(self.ema20[0]):
                self.close()
                self.trade_log.append({
                    "dt": self.data.datetime.datetime(),
                    "type": "EXIT",
                    "price": close,
                    "ema20": float(self.ema20[0]),
                })
                self.stop_price = None
                return
