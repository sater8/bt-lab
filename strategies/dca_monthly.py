import backtrader as bt


class Strategy(bt.Strategy):
    params = dict(
        # Día del mes en el que se ejecuta la compra
        monthly_day=1,

        # Debug opcional
        debug=False,

        # 👇 Estos son solo para ser compatibles con el runner de Pablo
        sizing_mode="all_in",   # no lo usamos aquí
        fixed_stake=0.0,        # no lo usamos aquí
        stake_pct=1.0,          # no lo usamos aquí
    )

    def __init__(self):
        # Para asegurarnos de que solo compramos una vez por mes,
        # aunque el timeframe sea 4h, 1h, etc.
        self.last_buy_year = None
        self.last_buy_month = None

    def next(self):
        dt = self.data.datetime.datetime(0)

        # Solo actuamos el día "monthly_day" (por defecto, 1 de cada mes)
        if dt.day != self.p.monthly_day:
            return

        # Evitar compras duplicadas en el mismo mes
        if self.last_buy_year == dt.year and self.last_buy_month == dt.month:
            return

        # Dinero disponible en la cuenta (lo habrá añadido MonthlyDeposit)
        cash = float(self.broker.get_cash())
        if cash <= 0:
            return

        price = float(self.data.close[0])
        if price <= 0:
            return

        # Vamos ALL-IN sobre el cash disponible → DCA puro de lo que haya entrado
        size = cash / price
        if size <= 0:
            return

        # Ejecutar compra (slippage, fees, etc. los maneja el middleware)
        self.buy(size=size)

        # Guardamos mes/año de la última compra
        self.last_buy_year = dt.year
        self.last_buy_month = dt.month

        if self.p.debug:
            print(f"[DCA_MONTHLY] {dt} | cash={cash:.2f} price={price:.2f} size={size:.8f}")