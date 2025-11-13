import argparse
import importlib.util
import os
import sys
import backtrader as bt
import pandas as pd
from datetime import datetime
import os, sys
# --- Añadimos la carpeta 'src' al path para poder importar tools.exchange_rules ---
sys.path.append(os.path.join(os.path.dirname(__file__), "src"))

from tools.exchange_rules import ensure_exchange_rules




def compute_buy_and_hold(data_path: str, capital: float):
    """
    Calcula el resultado de comprar en la 1ª vela del CSV y holdear hasta la última,
    sin comisiones ni rebalanceos.
    Devuelve (final_value, pct, first_close, last_close).
    """
    df = pd.read_csv(data_path)
    # Detectar columna 'close' de forma robusta (Close / close / etc.)
    lower = {c.lower(): c for c in df.columns}
    close_col = lower.get('close')
    if close_col is None:
        raise ValueError("No se encontró la columna 'close' en el CSV.")

    closes = pd.to_numeric(df[close_col], errors='coerce').dropna()
    if len(closes) < 2:
        raise ValueError("El CSV no tiene suficientes velas para Buy&Hold.")

    first_close = float(closes.iloc[0])
    last_close  = float(closes.iloc[-1])
    final_value = capital * (last_close / first_close)
    pct = (final_value / capital - 1.0) * 100.0
    return final_value, pct, first_close, last_close


def load_strategy(strategy_path: str):
    """Carga una estrategia .py desde la carpeta strategies."""
    if not os.path.exists(strategy_path):
        raise FileNotFoundError(f"Estrategia no encontrada: {strategy_path}")
    spec = importlib.util.spec_from_file_location("strategy_module", strategy_path)
    strategy_module = importlib.util.module_from_spec(spec)
    sys.modules["strategy_module"] = strategy_module
    spec.loader.exec_module(strategy_module)
    return strategy_module


SAT_MAP = {
    "ruptura_retest": 0.45,  # Estrategia A
    "pullback_ema20": 0.40,  # Estrategia B
    # si añades C/D/F: "rango_vwap":0.35, "squeeze_momo":0.30, "reversion_bb":0.30
}

def run_backtest(data_path, strategy_path, capital, commission, symbol, strategy_name, pass_params=None):
    data = bt.feeds.GenericCSVData(
        dataname=data_path,
        dtformat='%Y-%m-%d %H:%M:%S',
        timeframe=bt.TimeFrame.Minutes,
        compression=240,
        headers=True,
        datetime=0, open=1, high=2, low=3, close=4, volume=5,
        openinterest=-1
    )
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(capital)
    cerebro.broker.setcommission(commission=commission)

    strategy_module = load_strategy(strategy_path)
    if not hasattr(strategy_module, "Strategy"):
        raise AttributeError("Tu archivo de estrategia debe tener una clase llamada 'Strategy'")

    # Pasar parámetros si la estrategia los soporta
    pass_params = pass_params or {}
    try:
        cerebro.addstrategy(strategy_module.Strategy, **pass_params)
    except TypeError:
        cerebro.addstrategy(strategy_module.Strategy)  # compatibilidad

    cerebro.adddata(data)
    print(f"💰 Starting Portfolio Value: {cerebro.broker.getvalue():.2f}")

    results = cerebro.run()
    strat = results[0]

    print(f"🏁 Final Portfolio Value:   {cerebro.broker.getvalue():.2f}")

    try:
        bh_final, bh_pct, bh_first, bh_last = compute_buy_and_hold(data_path, capital)
        print(f"📦 Buy & Hold ({symbol}): {capital:.2f} → {bh_final:.2f} ({bh_pct:+.2f}%) "
              f"[{bh_first:.4f} → {bh_last:.4f}]")
    except Exception as e:
        print(f"⚠️ No se pudo calcular Buy & Hold: {e}")

    if hasattr(strat, 'trade_log') and len(strat.trade_log) > 0:
        df = pd.DataFrame(strat.trade_log)
        if not os.path.exists("results"):
            os.makedirs("results")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = f"results/{symbol}_{strategy_name}_{timestamp}.csv"
        try:
            bh_final, bh_pct, bh_first, bh_last = compute_buy_and_hold(data_path, capital)
            df_summary = pd.DataFrame([{
                "Resumen": "Buy & Hold",
                "Precio entrada": bh_first,
                "Precio salida": bh_last,
                "Beneficio (%)": round(bh_pct, 2),
                "Saldo final (simulado)": round(bh_final, 2)
            }])
            df = pd.concat([df, df_summary], ignore_index=True)
        except Exception as e:
            print(f"⚠️ No se pudo calcular Buy & Hold para CSV: {e}")
        df.to_csv(out_path, index=False)
        print(f"📊 Resultados guardados en: {out_path}")
    else:
        print("⚠️ La estrategia no generó 'trade_log' o no hubo operaciones.")

    try:
        cerebro.plot(style='candlestick')
    except Exception:
        print("⚠️ No se pudo mostrar el gráfico (entorno sin interfaz).")


def main():
    parser = argparse.ArgumentParser(description="Ejecuta un backtest con Backtrader")
    parser.add_argument("--symbols", required=True,
                        help="Símbolos separados por coma (p.ej. BTCUSDT,ADAUSDT,XRPUSDT)")
    parser.add_argument("--strategy", required=True, help="Nombre del archivo de estrategia (sin .py)")
    parser.add_argument("--capital", type=float, default=None,
                        help="Capital inicial por símbolo (modo simple)")
    parser.add_argument("--account", type=float, default=None,
                        help="Cuenta total en EUR/USDT para calcular satélite por estrategia")
    parser.add_argument("--split", type=str, default=None,
                        help="Pesos por símbolo en satélite, ej: ADAUSDT:0.5,XRPUSDT:0.5")
    parser.add_argument("--commission", type=float, default=0.001,
                        help="Comisión por trade (ej. 0.001 equivale al 0.10%%)")
    args = parser.parse_args()

    # === 1️⃣ Lista de símbolos ===
    symbols = [s.strip().upper() for s in args.symbols.split(",")]

    # === 2️⃣ Descarga y cachea reglas del exchange ===
    from tools.exchange_rules import ensure_exchange_rules
    ex_rules = ensure_exchange_rules(symbols)   # ← NUEVO

    strategy_name = args.strategy
    data_dir = "data"

    # === 3️⃣ Capitales ===
    capitals = {}
    pass_params = {
        "max_alloc_pct": 0.85,
        "onramp_max": 5,
        "onramp_risk_cap": 0.0075,
        "ex_rules": ex_rules    # ← NUEVO: se pasa todo el diccionario de reglas
    }

    if args.account is not None:
        sat_pct = SAT_MAP.get(strategy_name, 0.40)
        sat_total = args.account * sat_pct
        if args.split:
            parts = dict(p.split(":") for p in args.split.split(","))
            weights = {k.upper(): float(v) for k, v in parts.items()}
        else:
            w = 1.0 / len(symbols)
            weights = {s: w for s in symbols}
        for s in symbols:
            capitals[s] = sat_total * weights.get(s, 0.0)
    elif args.capital is not None:
        for s in symbols:
            capitals[s] = float(args.capital)
    else:
        raise SystemExit("Debes pasar --account (recomendado) o --capital.")

    # === 4️⃣ Ejecución por símbolo ===
    for symbol in symbols:
        print("\n" + "=" * 70)
        print(f"🚀 {symbol} | estrategia {strategy_name} | capital asignado: {capitals[symbol]:.2f}")
        print("=" * 70)

        data_path = os.path.join(data_dir, f"{symbol}_4h.csv")
        strategy_path = os.path.join("strategies", f"{strategy_name}.py")

        if not os.path.exists(data_path):
            print(f"❌ CSV no encontrado: {data_path}")
            continue

        # --- 5️⃣ Copiamos los params base y añadimos el nombre del símbolo ---
        pass_params_sym = dict(pass_params)
        pass_params_sym["symbol_name"] = symbol

        run_backtest(data_path, strategy_path, capitals[symbol],
                     args.commission, symbol, strategy_name, pass_params=pass_params_sym)

if __name__ == "__main__":
    main()
