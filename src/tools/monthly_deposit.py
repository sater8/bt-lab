# src/tools/monthly_deposit.py
import backtrader as bt

class MonthlyDeposit(bt.Analyzer):
    """
    Añade 'amount' de cash al broker al empezar cada nuevo mes.
    Sirve como DCA mensual simple.
    """

    params = dict(
        amount=0.0
    )

    def start(self):
        self._last_month = None
        self._num_deposits = 0

    def next(self):
        if self.p.amount <= 0:
            return

        dt = self.data.datetime.datetime(0)
        month = dt.month
        year = dt.year

        if self._last_month is None:
            # Primer dato: solo inicializamos, no metemos depósito aún
            self._last_month = (year, month)
            return

        if (year, month) != self._last_month:
            # Mes nuevo → añadimos cash
            self.strategy.broker.add_cash(self.p.amount)
            self._num_deposits += 1
            self._last_month = (year, month)

    def get_analysis(self):
        total_deposited = self._num_deposits * self.p.amount
        return dict(
            monthly_amount=self.p.amount,
            num_deposits=self._num_deposits,
            total_deposited=total_deposited
        )
