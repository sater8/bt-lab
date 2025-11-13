import backtrader as bt
import pandas as pd
import numpy as np
from math import floor
from tools.exchange_rules import bt_conform_market_order
import backtrader as bt, pandas as pd
from math import floor


# === Indicadores auxiliares ===
def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()

def _rma(s: pd.Series, n: int) -> pd.Series:
    alpha = 1.0 / float(n)
    return s.ewm(alpha=alpha, adjust=False, min_periods=n).mean()

def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_c = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_c).abs()
    tr3 = (df["low"] - prev_c).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

def _atr14(df: pd.DataFrame) -> pd.Series:
    return _rma(_true_range(df), 14)

def _rsi14(close: pd.Series) -> pd.Series:
    d = close.diff()
    gain = d.clip(lower=0)
    loss = (-d).clip(lower=0)
    avg_gain = _rma(gain, 14)
    avg_loss = _rma(loss, 14)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


# === Señal — Pullback sano a EMA20 ===
def signal_pullback_ema20(df: pd.DataFrame,
                          rsi_floor: int = 45,
                          wick_ratio_min: float = 0.75) -> pd.Series:
    """
    Devuelve Serie booleana 'entry' en la vela t (entrada real sería en t+1).
    """
    df = df.copy().sort_index()
    df["ema20"] = _ema(df["close"], 20)
    df["ema50"] = _ema(df["close"], 50)
    df["atr14"] = _atr14(df)
    df["rsi14"] = _rsi14(df["close"])

    tendencia = (df["ema20"] > df["ema50"]) & (df["close"] > df["ema50"])
    toca_ema20 = (df["low"] <= df["ema20"] + 0.1 * df["atr14"]) & (df["close"] >= df["ema20"])
    fuerza_ok = (df["rsi14"] >= rsi_floor)

    lower_wick = np.maximum(0.0, np.minimum(df["open"], df["close"]) - df["low"])
    body = (df["close"] - df["open"]).abs()
    wick_ratio = lower_wick / body.replace(0, np.nan)
    rechazo_ok = (wick_ratio >= wick_ratio_min).fillna(False)

    entry = (tendencia & toca_ema20 & fuerza_ok & rechazo_ok).fillna(False)
    return entry


# === Estrategia Backtrader con control de riesgo real ===
class Strategy(bt.Strategy):
    """
    Estrategia B — Pullback a EMA20 (4H) — VERSIÓN LIMPIA
    - Solo señales y sizing por riesgo. Nada de exchange/fees aquí.
    - Salida provisional: cierre < EMA50.
    """
    params = dict(
        risk_pct=0.01, sl_pct=0.08, max_alloc_pct=0.85,
        rsi_floor=45, wick_ratio_min=0.75,
        onramp_max=5, onramp_risk_cap=0.0075
    )

    def __init__(self):
        self.data_close = self.datas[0].close
        self.trade_log = []
        self.ema50 = bt.ind.EMA(self.data_close, period=50)
        self.entry_price = 0.0
        self.entry_qty = 0.0
        self.signals = None
        self.trades_count = 0

    def start(self):
        df = pd.DataFrame({
            "open": self.data.open.array,
            "high": self.data.high.array,
            "low": self.data.low.array,
            "close": self.data.close.array,
            "volume": self.data.volume.array
        })
        self.signals = signal_pullback_ema20(
            df, rsi_floor=self.params.rsi_floor, wick_ratio_min=self.params.wick_ratio_min
        )

    def next(self):
        date = self.data.datetime.datetime(0)
        close = float(self.data_close[0])
        idx = len(self) - 1
        if self.signals is None or idx >= len(self.signals):
            return

        signal_now = bool(self.signals.iloc[idx])

        if not self.position and signal_now:
            account_eur = float(self.broker.getvalue())
            cash = float(self.broker.get_cash())

            base_risk = float(self.params.risk_pct)
            risk_eff = min(base_risk, float(self.params.onramp_risk_cap)) if self.trades_count < int(self.params.onramp_max) else base_risk

            qty_by_risk  = (account_eur * risk_eff) / (close * float(self.params.sl_pct))
            qty = floor(qty_by_risk * 1000) / 1000.0
            if qty <= 0 or qty * close > cash:
                return

            self.buy(size=qty)
            self.entry_price = close
            self.entry_qty = qty
            self.trades_count += 1

            self.trade_log.append({
                "Fecha entrada": date, "Tipo": "BUY",
                "Precio entrada": close, "Tamaño": qty,
                "Riesgo (%)": round(risk_eff*100, 2),
                "Saldo antes (€)": round(account_eur, 2),
                "Notional entrada (€)": round(qty*close, 2)
            })

        elif self.position and close < float(self.ema50[0]):
            qty_open = float(self.position.size) if hasattr(self.position, "size") else self.entry_qty
            self.close()
            profit_eur_bruto = (close - self.entry_price) * qty_open
            profit_pct = (close - self.entry_price) / self.entry_price * 100.0

            self.trade_log.append({
                "Fecha salida": date, "Tipo": "SELL",
                "Precio salida": close,
                "Beneficio (%)": round(profit_pct, 2),
                "PnL bruto (€)": round(profit_eur_bruto, 2),
                "Saldo después (€)": round(float(self.broker.getvalue()), 2)
            })

            self.entry_price = 0.0
            self.entry_qty = 0.0
