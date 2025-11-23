import backtrader as bt
from tools.sizing import PabloSizingMixin


class Strategy(PabloSizingMixin):
    params = dict(
        # --- Parámetros de la estrategia (SEÑAL) ---
        atr_mult=2.0,              # stop = close - 2*ATR
        bb_period=20,
        bb_dev=2.0,
        squeeze_threshold=0.12,    # (banda superior - inferior) / close
        vol_period=20,
        trend_filter=True,         # require EMA20 > EMA50
        body_ratio_min=0.5,        # vela breakout debe tener cuerpo >= 50% del rango

        # ¿Permitir añadir más posición mientras ya hay una abierta?
        allow_pyramiding=False,    # si True, puede hacer varias compras

        # --- IMPORTANTE ---
        # Los parámetros de sizing vienen de PabloSizingMixin:
        #   sizing_mode  : "all_in", "fixed", "percent"
        #   fixed_stake  : cantidad fija en moneda
        #   stake_pct    : porcentaje del cash (0–1)
        #
        # NO usamos ya risk_pct para el size. El riesgo por ATR solo se usa
        # para colocar el stop (atr_mult), no para dimensionar la posición.
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

        # Logs y stop dinámico
        self.trade_log = []
        self.stop_price = None

    # ------------------------------
    #   LÓGICA DE VELA BREAKOUT
    # ------------------------------
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

    # ------------------------------
    #             NEXT
    # ------------------------------
    def next(self):
        close = float(self.data.close[0])
        atr = float(self.atr[0])
        bb_w = float(self.bb_width[0])

        if atr <= 0:
            return

        have_position = bool(self.position and self.position.size != 0)

        # ==========================
        #        ENTRADAS
        # ==========================
        # Si allow_pyramiding = False → solo entra si no hay posición.
        # Si allow_pyramiding = True  → puede añadir más posición aunque ya haya.
        can_enter = (not have_position) or self.p.allow_pyramiding

        if can_enter:
            cond_squeeze = bb_w <= self.p.squeeze_threshold
            cond_breakout = close > float(self.bb.lines.top[0]) and self.is_breakout_candle()
            cond_vol = float(self.data.volume[0]) > float(self.vol_ma[0])

            cond_trend = True
            if self.p.trend_filter:
                cond_trend = float(self.ema20[0]) > float(self.ema50[0])

            if cond_squeeze and cond_breakout and cond_vol and cond_trend:
                # --- SIZING SIMPLE (PabloSizingMixin) ---
                price = close
                size = self.get_stake_size(price)  # usa sizing_mode / fixed_stake / stake_pct

                if size <= 0:
                    return

                # stop dinámico basado en ATR
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
        #        SALIDAS
        # ==========================
        if have_position:
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
