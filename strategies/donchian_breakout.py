import backtrader as bt


class Strategy(bt.Strategy):
    params = dict(
        # --- Parámetros de riesgo y stop ---
        risk_pct=0.01,          # % del equity que queremos arriesgar
        atr_mult=2.0,           # Stop ATR
        entry_window=55,        # Máximo Donchian para entrar
        exit_window=20,         # Mínimo Donchian para salir

        # --- Estos parámetros SON OBLIGATORIOS en bt-lab ---
        max_alloc_pct=None,
        onramp_max=None,
        onramp_risk_cap=None,
    )

    def __init__(self):

        # === Indicadores Core ===
        self.high_entry = bt.ind.Highest(self.data.high, period=self.p.entry_window)
        self.low_exit = bt.ind.Lowest(self.data.low, period=self.p.exit_window)
        self.atr = bt.ind.ATR(self.data, period=14)

        # === State ===
        self.trade_log = []
        self.stop_price = None


    def next(self):
        close = float(self.data.close[0])
        high  = float(self.data.high[0])
        atr   = float(self.atr[0])

        if atr <= 0:
            return

        # =============================
        #        SIN POSICIÓN
        # =============================
        if not self.position:

            # Breakout Donchian REAL (HIGH rompe el máximo previo)
            breakout = high > float(self.high_entry[-1])

            if breakout:

                equity = float(self.broker.getvalue())
                risk_amount = equity * self.p.risk_pct

                risk_per_unit = self.p.atr_mult * atr
                if risk_per_unit <= 0:
                    return

                # >>> Clave: reducir tamaño para que el middleware NO bloquee <<<
                size = (risk_amount / risk_per_unit) * 0.5

                if size <= 0:
                    return

                # Stop dinámico ATR
                self.stop_price = close - self.p.atr_mult * atr

                self.buy(size=size)

                self.trade_log.append({
                    "dt": self.data.datetime.datetime(),
                    "type": "BUY",
                    "price": close,
                    "size": size,
                    "highest_entry": float(self.high_entry[-1]),
                    "atr": atr
                })

                return


        # =============================
        #        CON POSICIÓN
        # =============================
        else:

            # --- STOP ATR dinámico ---
            if self.stop_price and close <= self.stop_price:
                self.close()
                self.trade_log.append({
                    "dt": self.data.datetime.datetime(),
                    "type": "STOP",
                    "price": close,
                    "stop_price": self.stop_price
                })
                self.stop_price = None
                return

            # --- Salida Donchian 20 con buffer ATR (evita ruido) ---
            exit_level = float(self.low_exit[0]) + atr * 0.2

            if close < exit_level:
                self.close()
                self.trade_log.append({
                    "dt": self.data.datetime.datetime(),
                    "type": "EXIT",
                    "price": close,
                    "low_exit_buffer": exit_level
                })
                self.stop_price = None
                return
