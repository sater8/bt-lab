import backtrader as bt
import pandas as pd
import numpy as np
from math import floor
from tools.exchange_rules import bt_conform_market_order



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
    Estrategia Ruptura + Retest (4H)
    - Compra en señales.
    - Tamaño por riesgo y cap por orden (buffer 15%: max_alloc_pct=0.85).
    - On-ramp: primeras 5 entradas con riesgo capado a 0.75%.
    - Salida: cierre < EMA50 (de momento; OCO lo metemos luego).
    """
    params = dict(
    risk_pct=0.01, sl_pct=0.08, max_alloc_pct=0.85, onramp_max=5, onramp_risk_cap=0.0075,
    ex_rules=None,          # ← NUEVO
    symbol_name=None        # ← NUEVO
    )

    def __init__(self):
        self.data_close = self.datas[0].close
        self.trade_log = []
        self.ema50 = bt.ind.EMA(self.data_close, period=50)
        self.entry_price = None
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
        self.signals = signal_ruptura_retest(df)

    def next(self):
        date = self.data.datetime.datetime(0)
        close = self.data_close[0]
        idx = len(self) - 1
        if self.signals is None or idx >= len(self.signals):
            return

        signal_now = bool(self.signals.iloc[idx])

        # --- ENTRADA ---
        if not self.position and signal_now:
            account_eur = self.broker.getvalue()
            cash = self.broker.get_cash()

            sl_pct = self.params.sl_pct
            base_risk = self.params.risk_pct
            # on-ramp
            risk_pct_eff = min(base_risk, self.params.onramp_risk_cap) if self.trades_count < self.params.onramp_max else base_risk

            riesgo_eur = account_eur * risk_pct_eff
            qty_by_risk = riesgo_eur / (close * sl_pct)

            # cap por orden (buffer 15%)
            qty_by_alloc = (account_eur * self.params.max_alloc_pct) / close

            qty = floor(min(qty_by_risk, qty_by_alloc) * 1000) / 1000
            if qty <= 0:
                return

            # coste con comisión
            fee = self.broker.getcommissioninfo(self.data).p.commission
            cost = qty * close * (1 + fee)
            if cost > cash:
                return
            
            # --- aplicar reglas del exchange (MARKET) y cap de nueva orden ---
            cap_new = self.broker.getvalue() * self.params.max_alloc_pct
            ok, qty_adj, notional_adj, reason = bt_conform_market_order(
                rules = (self.params.ex_rules or {}).get(self.params.symbol_name, {}),
                side = "buy", ref_price = close, qty = qty, cap_notional = cap_new
            )
            if not ok or qty_adj <= 0:
                self.trade_log.append({
                    "Fecha entrada": date, "Tipo": "SKIP",
                    "Motivo": f"exchange_rules: {reason}", "Cap orden (€)": round(cap_new,2)
                })
                return

            qty = qty_adj


            self.buy(size=qty)
            self.entry_price = close
            self.trades_count += 1
            self.trade_log.append({
                "Fecha entrada": date,
                "Tipo": "BUY",
                "Precio entrada": close,
                "Tamaño": qty,
                "Riesgo (%)": round(risk_pct_eff * 100, 2),
                "Saldo antes (€)": round(account_eur, 2)
            })

        # --- SALIDA ---
        elif self.position and close < self.ema50[0]:
            self.close()
            # FIX PnL €:
            qty = float(self.position.size) if hasattr(self.position, "size") else 0.0
            profit_eur = (close - self.entry_price) * qty
            profit_pct = (close - self.entry_price) / self.entry_price * 100
            self.trade_log.append({
                "Fecha salida": date,
                "Tipo": "SELL",
                "Precio salida": close,
                "Beneficio (%)": round(profit_pct, 2),
                "Beneficio (€)": round(profit_eur, 2),
                "Saldo después (€)": round(self.broker.getvalue(), 2)
            })
            self.entry_price = None
