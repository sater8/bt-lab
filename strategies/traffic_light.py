# traffic_light.py
# Estrategia Traffic-Light 4H para cualquier símbolo (BTC/ADA/XRP)

import backtrader as bt
import pandas as pd
import numpy as np

# =====================
# Helpers
# =====================
def ema(series, n):
    return series.ewm(span=n, adjust=False).mean()

def rsi_wilder(series, n=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.ewm(alpha=1/n, adjust=False).mean()
    roll_down = down.ewm(alpha=1/n, adjust=False).mean()
    rs = roll_up / roll_down
    return 100 - (100 / (1 + rs))

def atr_wilder(df, n=14):
    high = df['high']
    low = df['low']
    close = df['close']
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

# =====================
# Estado Traffic-Light
# =====================
class TLState:
    def __init__(self, color, risk):
        self.color = color
        self.risk = risk

# =====================
# Estrategia principal
# =====================
class Strategy(bt.Strategy):
    params = dict(sl_pct=0.08)  # SL teorico 8%

    def start(self):
        data = self.datas[0]
        o = data.open.array
        h = data.high.array
        l = data.low.array
        c = data.close.array
        v = data.volume.array

        df = pd.DataFrame({
            'open': o,
            'high': h,
            'low': l,
            'close': c,
            'volume': v
        })

        df['ema20'] = ema(df['close'], 20)
        df['ema50'] = ema(df['close'], 50)
        mb = df['close'].rolling(20).mean()
        sd = df['close'].rolling(20).std()
        df['bb_mid'] = mb
        df['rsi'] = rsi_wilder(df['close'], 14)
        df['atr'] = atr_wilder(df, 14)
        df['vol_ma2'] = df['volume'].rolling(2).mean()

        self.df = df
        self.position_open = False
        self.trade_log = []

    def eval_state(self, i):
        row = self.df.iloc[i]
        close = row.close
        ema20 = row.ema20
        ema50 = row.ema50
        rsi = row.rsi
        bb_mid = row.bb_mid
        vol = row.volume
        vol_ma2 = row.vol_ma2

        # Estado VERDE
        cond_green = (
            close > ema50 and
            ema20 > ema50 and
            50 <= rsi <= 70 and
            close >= bb_mid and
            (close > self.df.close.iloc[i-1] or vol >= vol_ma2)
        )
        if cond_green:
            return TLState('green', 0.0125)

        # AMARILLO
        cond_yellow = (
            ema50 <= close <= ema20 or
            45 <= rsi < 50
        )
        if cond_yellow:
            return TLState('yellow', 0.01)

        # NARANJA
        cond_orange = (
            close < ema20 or
            rsi < 45
        )
        if cond_orange:
            return TLState('orange', 0.0075)

        # ROJO
        return TLState('red', 0.0075)

    def next(self):
        i = len(self) - 1
        if i < 50:
            return

        state = self.eval_state(i)
        close = self.data.close[0]

        # Ultra-defensive (genérico)
        if state.color == 'red':
            day_low = self.df['low'].rolling(6).min().iloc[i]
            vol_spike = self.df.volume.iloc[i] > self.df.vol_ma2.iloc[i]
            if close < day_low or vol_spike:
                state = TLState('ultra', 0.005)

        dfrow = self.df.iloc[i]
        atr = dfrow.atr

        if not self.position:
            if state.color in ['green', 'yellow', 'ultra']:
                cash = self.broker.get_cash()
                risk_val = self.broker.getvalue() * state.risk
                qty = (risk_val / (close * self.p.sl_pct))
                qty = max(0, qty)
                self.buy(size=qty)
                self.entry_price = close
                self.position_open = True
                self.trade_log.append({'i': i, 'type': 'BUY', 'price': close, 'risk': state.risk})
        else:
            if state.color in ['red', 'orange', 'ultra']:
                self.close()
                self.position_open = False
                pnl = close - self.entry_price
                self.trade_log.append({'i': i, 'type': 'SELL', 'price': close, 'pnl': pnl})
