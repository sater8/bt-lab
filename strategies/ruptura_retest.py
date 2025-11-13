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

def _sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()

def _bollinger_width(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.Series:
    mid = _sma(close, n)
    std = close.rolling(n, min_periods=n).std(ddof=0)
    up = mid + k * std
    dn = mid - k * std
    return up - dn


# === Función de señal ===
def signal_ruptura_retest(df: pd.DataFrame,
                          n_donchian: int = 20,
                          retest_candles_max: int = 2,
                          squeeze_pctile_max: int = 25) -> pd.Series:

    df = df.copy().sort_index()
    df["ema20"] = _ema(df["close"], 20)
    df["ema50"] = _ema(df["close"], 50)
    df["ema200"] = _ema(df["close"], 200)
    df["atr14"] = _atr14(df)
    df["bb_width"] = _bollinger_width(df["close"], 20, 2.0)

    tendencia = (df["ema20"] > df["ema50"]) & (df["ema50"] > df["ema200"]) & (df["close"] > df["ema50"])
    don_hi_prev = df["high"].rolling(n_donchian).max().shift(1)
    ruptura = df["close"] > don_hi_prev
    q = df["bb_width"].rolling(200).quantile(squeeze_pctile_max / 100.0)
    squeeze_ok = df["bb_width"] <= q.fillna(method="bfill")

    armed = False
    level = np.nan
    ttl = 0
    entry = pd.Series(False, index=df.index)

    for i, idx in enumerate(df.index):
        if i == 0:
            continue

        if not armed:
            if bool(tendencia.iat[i] and ruptura.iat[i] and squeeze_ok.iat[i]):
                armed = True
                level = float(don_hi_prev.iat[i])
                ttl = int(retest_candles_max)
        else:
            ttl = max(ttl - 1, 0)
            if df["low"].iat[i] < level - 0.25 * df["atr14"].iat[i]:
                armed = False
                level = np.nan
                ttl = 0
                continue

            toca = df["low"].iat[i] <= level + 0.1 * df["atr14"].iat[i]
            recierra = df["close"].iat[i] >= level
            if toca and recierra:
                entry.iat[i] = True
                armed = False
                level = np.nan
                ttl = 0
                continue

            if ttl == 0:
                armed = False
                level = np.nan

    return entry.fillna(False)


# === Estrategia Backtrader con control de riesgo real ===
class Strategy(bt.Strategy):
    """
    Estrategia A — Ruptura + Retest (4H) — VERSIÓN LIMPIA
    - La estrategia solo calcula señales y tamaño por riesgo.
    - NO aplica reglas del exchange ni fees: eso lo hace el middleware global.
    - Salida provisional: cierre < EMA50 (LOCK completo vendrá luego).
    """
    params = dict(
        risk_pct=0.01,        # 1% por operación
        sl_pct=0.08,          # SL teórico 8% (para calcular qty)
        max_alloc_pct=0.85,   # el cap real lo impondrá el middleware (buffer 15%)
        onramp_max=5,
        onramp_risk_cap=0.0075
    )

    def __init__(self):
        self.data_close = self.datas[0].close
        self.trade_log = []
        self.ema50 = bt.ind.EMA(self.data_close, period=50)
        self.entry_price = None
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
        # Usa tu función original de señal del archivo
        self.signals = signal_ruptura_retest(df)

    def next(self):
        date = self.data.datetime.datetime(0)
        close = float(self.data_close[0])
        idx = len(self) - 1
        if self.signals is None or idx >= len(self.signals):
            return

        signal_now = bool(self.signals.iloc[idx])

        # ENTRADA (solo sizing por riesgo; el middleware ajusta exchange/fees/cap)
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

        # SALIDA temporal
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

            self.entry_price = None
            self.entry_qty = 0.0
