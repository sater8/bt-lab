#!/usr/bin/env python3
"""
Bollinger Breakout (bt-lab version) – Real-Time Discord Bot
-----------------------------------------------------------
Replica EXACTAMENTE la estrategia de:
    strategies/bol_breakout.py
"""

import os
import json
import time
import requests
import numpy as np
import pandas as pd

STATE_PATH = "state_bol_breakout.json"
BINANCE = "https://api.binance.com"

# Webhook (configúralo en variable de entorno SIEMPRE)
DISCORD_WEBHOOK_URL = "https://discordapp.com/api/webhooks/1440298281296068649/ZvnrO6MfJHtIM48oMRLC25CHvERBN7AmVA9VeidE5OSazuF4TSaWxleF0ZreypFUCHS-"

# Parámetros idénticos a la estrategia real
RISK_PCT = 0.01      # 1% de riesgo
ATR_MULT = 2.0       # stop = close - 2*ATR

HEARTBEAT_FILE = "bot_heartbeat.txt"



# ------------------------------------------------------
# UTILIDADES BÁSICAS
# ------------------------------------------------------

def fetch_klines(symbol, interval="4h", limit=300):
    url = f"{BINANCE}/api/v3/klines"
    params = dict(symbol=symbol, interval=interval, limit=limit)
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    raw = r.json()

    cols = [
        "open_time","open","high","low","close","volume",
        "close_time","quote_asset_volume","n_trades",
        "tbbav","tbqav","ignore"
    ]

    df = pd.DataFrame(raw, columns=cols)
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)

    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
    return df


def send_discord(msg: str):
    if not DISCORD_WEBHOOK_URL:
        print("[NO WEBHOOK] →", msg)
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": msg}, timeout=10)
    except Exception as e:
        print(f"[Discord ERROR] {e}")


def load_state():
    if not os.path.exists(STATE_PATH):
        return {}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ------------------------------------------------------
# INDICADORES
# ------------------------------------------------------

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def atr(df, period=14):
    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def bollinger(df, period=20, dev=2.0):
    mid = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    upper = mid + dev * std
    lower = mid - dev * std
    return mid, upper, lower


# ------------------------------------------------------
# LÓGICA BREAKOUT
# ------------------------------------------------------

def is_breakout_candle(row):
    o = row["open"]
    c = row["close"]
    h = row["high"]
    l = row["low"]

    body = abs(c - o)
    range_ = max(h - l, 1e-8)

    return (body / range_ >= 0.5) and (c > o)


# ------------------------------------------------------
# EVALUACIÓN DE SEÑAL
# ------------------------------------------------------

def evaluate_boll_breakout(df, state):

    last = df.iloc[-2]   # última vela cerrada
    prev = df.iloc[-3]

    last_open_ms = int(last["open_time"].timestamp() * 1000)

    if last_open_ms == state["last_open_ms"]:
        return state, None

    ema20 = df["ema20"].iloc[-2]
    ema50 = df["ema50"].iloc[-2]

    atr14 = df["atr14"].iloc[-2]
    bb_top = df["bb_top"].iloc[-2]
    bb_bot = df["bb_bot"].iloc[-2]
    vol = last["volume"]
    vol_ma = df["vol_ma"].iloc[-2]
    close = last["close"]

    bb_width = (bb_top - bb_bot) / (close + 1e-8)

    # --------------------------------------------------
    # ENTRADA
    # --------------------------------------------------
    if state["position"] == "FLAT":

        cond_squeeze = bb_width <= 0.12
        cond_breakout = close > bb_top and is_breakout_candle(last)
        cond_vol = vol > vol_ma
        cond_trend = ema20 > ema50

        if cond_squeeze and cond_breakout and cond_vol and cond_trend:

            # stop idéntico a backtester
            stop_price = close - ATR_MULT * atr14

            # ---- % UNIVERSAL DE INVERSIÓN ----
            invest_pct = (RISK_PCT * close / (ATR_MULT * atr14)) * 100

            msg = (
                f"🟢 **ENTRADA LONG**\n"
                f"Precio entrada: `{close:.4f}`\n"
                f"BB width: `{bb_width:.4f}`\n"
                f"📈 Inversión aprox: `{invest_pct:.2f}%` de tu capital"
            )

            state["position"] = "LONG"
            state["stop_price"] = float(stop_price)
            state["last_open_ms"] = last_open_ms
            return state, msg

    # --------------------------------------------------
    # SALIDA
    # --------------------------------------------------
    else:  # LONG

        if close < ema20:
            msg = (
                f"🔴 **SALIDA (WEAK EXIT)**\n"
                f"Cierre: `{close:.4f}`\n"
                f"EMA20: `{ema20:.4f}`"
            )

            state["position"] = "FLAT"
            state["stop_price"] = None
            state["last_open_ms"] = last_open_ms
            return state, msg

    state["last_open_ms"] = last_open_ms
    return state, None


# ------------------------------------------------------
# LOOP PRINCIPAL
# ------------------------------------------------------

def run_bot(symbols, sleep=60):
    state = load_state()

    # Inicializar estado por símbolo
    for sym in symbols:
        if sym not in state:
            state[sym] = dict(position="FLAT", stop_price=None, last_open_ms=0)

    print(f"🚀 Bol Breakout live bot iniciado ({symbols})")
    send_discord(f"🤖 **Bot iniciado correctamente**\nMonedas: `{symbols}`")

    # --- HEARTBEAT terminal (cada 4 horas) ---
    last_hb_print = time.time()
    HEARTBEAT_PRINT_INTERVAL = 4 * 60 * 60  # 4 horas

    while True:
        for sym in symbols:
            try:
                df = fetch_klines(sym)

                # Indicadores
                df["ema20"] = ema(df["close"], 20)
                df["ema50"] = ema(df["close"], 50)
                df["atr14"] = atr(df, 14)
                df["vol_ma"] = df["volume"].rolling(20).mean()

                mid, top, bot = bollinger(df, 20, 2.0)
                df["bb_mid"] = mid
                df["bb_top"] = top
                df["bb_bot"] = bot

                new_state, msg = evaluate_boll_breakout(df, state[sym])
                state[sym] = new_state

                if msg:
                    send_discord(f"**{sym}**\n" + msg)

            except Exception as e:
                print(f"[ERROR {sym}] {e}")

        save_state(state)

        # --- Escribimos heartbeat a archivo (último latido en timestamp) ---
        try:
            with open(HEARTBEAT_FILE, "w") as f:
                f.write(str(time.time()))
        except Exception as e:
            print(f"[ERROR HEARTBEAT FILE] {e}")

        # --- Heartbeat en terminal cada 4h ---
        now = time.time()
        if now - last_hb_print >= HEARTBEAT_PRINT_INTERVAL:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            print(f"[HEARTBEAT] El bot sigue funcionando – {ts}")
            last_hb_print = now

        time.sleep(sleep)




# ------------------------------------------------------
# MAIN
# ------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", type=str, default="BTCUSDT,ADAUSDT,XRPUSDT")
    parser.add_argument("--sleep", type=int, default=60)
    args = parser.parse_args()

    syms = [x.strip().upper() for x in args.symbols.split(",")]
    run_bot(syms, sleep=args.sleep)
