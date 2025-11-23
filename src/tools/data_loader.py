# src/tools/data_loader.py
import pandas as pd
import backtrader as bt

def detect_timeframe_and_compression(df: pd.DataFrame):
    """
    Detecta timeframe y compression a partir de las dos primeras filas del CSV.
    Asume que la columna datetime está en la primera columna.
    """
    if len(df) < 2:
        # fallback: daily
        return bt.TimeFrame.Days, 1

    dt0 = pd.to_datetime(df.iloc[0, 0])
    dt1 = pd.to_datetime(df.iloc[1, 0])
    delta = dt1 - dt0
    minutes = int(delta.total_seconds() // 60)

    if minutes <= 0:
        # fallback: daily
        return bt.TimeFrame.Days, 1

    # Hasta 1 día lo tratamos como minutos
    if minutes < 60 * 24:
        return bt.TimeFrame.Minutes, minutes
    else:
        days = max(1, minutes // (60 * 24))
        return bt.TimeFrame.Days, days
