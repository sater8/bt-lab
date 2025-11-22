import backtrader as bt
from datetime import datetime

class Strategy(bt.Strategy):
    params = dict(
        amount=300.0,   # cantidad fija a invertir cada mes
    )

    def __init__(self):
        self.last_month = None
        self.trade_log = []

    def next(self):
        dt = self.data.datetime.datetime()
        current_month = dt.month

        # Solo hacer la compra el día 1 de cada mes y solo una vez
        if dt.day == 1 and current_month != self.last_month:
            self.last_month = current_month

            close = float(self.data.close[0])
            cash = float(self.broker.getcash())

            # Si hay dinero suficiente
            amount = min(self.p.amount, cash)
            if amount <= 0:
                return

            size = amount / close

            self.buy(size=size)

            self.trade_log.append({
                "dt": dt,
                "type": "DCA_BUY",
                "price": close,
                "size": size,
                "amount_spent": amount
            })
