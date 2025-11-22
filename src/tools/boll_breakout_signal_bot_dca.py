#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import time
import json
import os
from datetime import datetime, timezone
import argparse
import numpy as np

STATE_FILE = "state_boll_dca.json"

DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1441729762237091934/KcqZ5fGPTr3d3aE9Ul6gIt2YvSpFc_de72ch_nxVgw569BBAdiZ36q9QrKCOwRNDjsnu"
BINANCE_ENDPOINT = "https://api.binance.com/api/v3/klines"


# ============================================================
# STATE MANAGEMENT
# ============================================================

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ============================================================
# DISCORD HELPERS
# ============================================================

def send_discord_signal(symbol, price, dt):
    msg = (
        "🚀 **Señal Boll Breakout detectada**\n"
        f"**Símbolo:** {symbol}\n"
        f"**Precio:** {price:,.2f}\n"
        f"**Fecha:** {dt}"
    )
    requests.post(DISCORD_WEBHOOK, json={"content": msg})

def send_discord_raw(message: str):
    requests.post(DISCORD_WEBHOOK, json={"content": message})


# ============================================================
# MARKET DATA
# ============================================================

def get_klines(symbol):
    params = {"symbol": symbol, "interval": "4h", "limit": 200}
    r = requests.get(BINANCE_ENDPOINT, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


# ============================================================
# SIGNAL DETECTION
# ============================================================

def detect_signal(candles):
    closes = np.array([float(c[4]) for c in candles])
    highs  = np.array([float(c[2]) for c in candles])
    lows   = np.array([float(c[3]) for c in candles])
    opens  = np.array([float(c[1]) for c in candles])
    vols   = np.array([float(c[5]) for c in candles])

    o = opens[-1]
    c = closes[-1]
    h = highs[-1]
    l = lows[-1]

    body = abs(c - o)
    candle_range = max(h - l, 1e-8)
    body_ratio = body / candle_range

    if len(closes) < 20:
        return False

    ma = closes[-20:].mean()
    std = closes[-20:].std()
    bb_top = ma + 2.0 * std
    bb_bottom = ma - 2.0 * std
    bb_width = (bb_top - bb_bottom) / (c + 1e-9)

    vol_ma = vols[-20:].mean()

    ema20 = closes[-20:].mean()
    ema50 = closes[-50:].mean() if len(closes) >= 50 else closes.mean()

    cond_squeeze = bb_width <= 0.12
    cond_breakout = (c > bb_top) and (body_ratio >= 0.5)
    cond_vol = vols[-1] > vol_ma
    cond_trend = ema20 > ema50

    return cond_squeeze and cond_breakout and cond_vol and cond_trend


# ============================================================
# BOT LOOP (CHECK EVERY HOUR)
# ============================================================

def run_bot(symbols):

    state = load_state()

    for sym in symbols:
        if sym not in state:
            state[sym] = {"last_signal_ts": None}

    print(f"Bot Boll Breakout iniciado para: {symbols}")
    print("Revisando velas cada 1 hora (timeframe 4H).")
    print("==============================================================")

    start_msg = (
        "🟢 **Bot Boll Breakout DCA iniciado**\n"
        f"Monedas monitorizadas: {', '.join(symbols)}\n"
        f"Timeframe: 4H\n"
        f"Hora de inicio: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    )
    send_discord_raw(start_msg)

    while True:
        for sym in symbols:
            try:
                klines = get_klines(sym)
                last = klines[-1]

                candle_open_ts = int(last[0])

                # evitar duplicados
                if state[sym]["last_signal_ts"] == candle_open_ts:
                    continue

                # detectar señal
                if detect_signal(klines):
                    dt = datetime.fromtimestamp(candle_open_ts/1000, tz=timezone.utc)\
                                 .strftime("%Y-%m-%d %H:%M:%S UTC")

                    price = float(last[4])
                    print(f"🚀 Señal detectada en {sym} @ {price}")
                    send_discord_signal(sym, price, dt)

                    state[sym]["last_signal_ts"] = candle_open_ts
                    save_state(state)

            except Exception as e:
                print(f"❌ Error con {sym}: {e}")
                send_discord_raw(f"⚠️ Error con {sym}: {e}")

        print("⏳ Esperando 1 hora…")
        time.sleep(60 * 60)      # <-- revisar cada 1 hora


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", required=True)
    args = parser.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",")]

    run_bot(syms)
