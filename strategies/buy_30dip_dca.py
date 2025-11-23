import backtrader as bt

class Strategy(bt.Strategy):

    params = dict(
        monthly_amount=300,
        dip_pct=0.30,
    )

    def __init__(self):
        self.initial_price = None     # precio de referencia (inicial o último buy)
        self.budget = 0
        self.current_month = None

    def next(self):
        dt = self.datas[0].datetime.datetime(0)
        price = self.datas[0].close[0]

        # --- establecer precio inicial ---
        if self.initial_price is None:
            self.initial_price = price   # primera vela del CSV
            self.current_month = dt.month
            return

        # --- detectar cambio de mes ---
        if dt.month != self.current_month:
            # añadir 300 USDT al budget
            self.budget += self.p.monthly_amount

            # añadir 300 USDT reales al broker
            self.broker.add_cash(self.p.monthly_amount)

            self.current_month = dt.month

        # si todavía no hay budget → no comprar nunca
        if self.budget <= 0:
            return

        # --- trigger de DIP ---
        dip_trigger = self.initial_price * (1 - self.p.dip_pct)

        # --- si el precio ha caído 30% desde el precio inicial o último buy ---
        if price <= dip_trigger:
            size = self.budget / price
            self.buy(size=size)

            # actualizar precio de referencia
            self.initial_price = price

            # resetear budget tras compra
            self.budget = 0
