import argparse
import importlib.util
import os
import sys
import backtrader as bt
import pandas as pd
from datetime import datetime

# --- Añadimos la carpeta 'src' al path para poder importar tools.exchange_rules ---
sys.path.append(os.path.join(os.path.dirname(__file__), "src"))

from tools.exchange_rules import ensure_exchange_rules
from tools.fees import default_fees_cfg, buyhold_fees
from tools.exec_middleware import enable_exec_middleware, FeesNetAnalyzer
from tools.slippage import default_slippage_cfg, default_latency_cfg


# ------------------------------------------------------
# COMPUTE BUY & HOLD
# ------------------------------------------------------
def compute_buy_and_hold(data_path: str, capital: float):
    """
    Calcula el resultado de comprar en la primera vela del CSV y holdear hasta la última,
    sin comisiones. Devuelve (final_value, pct, first_close, last_close).
    """
    df = pd.read_csv(data_path)

    # Detecta columna close de forma robusta
    lower = {c.lower(): c for c in df.columns}
    close_col = lower.get("close")
    if close_col is None:
        raise ValueError("No se encontró la columna 'close'.")

    closes = pd.to_numeric(df[close_col], errors="coerce").dropna()
    if len(closes) < 2:
        raise ValueError("El CSV no tiene suficientes velas para Buy&Hold.")

    first_close = float(closes.iloc[0])
    last_close = float(closes.iloc[-1])
    final_value = capital * (last_close / first_close)
    pct = (final_value / capital - 1.0) * 100.0

    return final_value, pct, first_close, last_close


# ------------------------------------------------------
# CARGAR ESTRATEGIA EXTERNA
# ------------------------------------------------------
def load_strategy(strategy_path: str):
    if not os.path.exists(strategy_path):
        raise FileNotFoundError(f"Estrategia no encontrada: {strategy_path}")

    spec = importlib.util.spec_from_file_location("strategy_module", strategy_path)
    strategy_module = importlib.util.module_from_spec(spec)
    sys.modules["strategy_module"] = strategy_module
    spec.loader.exec_module(strategy_module)
    return strategy_module


# ------------------------------------------------------
# SATÉLITES
# ------------------------------------------------------
SAT_MAP = {
    "ruptura_retest": 0.45,
    "pullback_ema20": 0.40,
}


# ------------------------------------------------------
# RUN BACKTEST (POR SÍMBOLO)
# ------------------------------------------------------
def run_backtest(
    data_path, strategy_path, capital, commission,
    symbol, strategy_name, pass_params=None,
    ex_rules=None, fees_cfg=None, slip_cfg=None, lat_cfg=None
):

    # === CEREBRO ===
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(capital)
    cerebro.broker.setcommission(commission=0.0)

    # === DATA FEED ===
    data = bt.feeds.GenericCSVData(
        dataname=data_path,
        dtformat="%Y-%m-%d %H:%M:%S",
        timeframe=bt.TimeFrame.Minutes,
        compression=240,  # 4H
        headers=True,
        datetime=0,
        open=1,
        high=2,
        low=3,
        close=4,
        volume=5,
        openinterest=-1,
    )

    cerebro.adddata(data, name=symbol)

    # === MIDDLEWARE GLOBAL (UNA VEZ, AQUÍ) ===
    enable_exec_middleware(
        cerebro,
        ex_rules=ex_rules,
        fees_cfg=fees_cfg,
        max_alloc_pct=0.85,
        slip_cfg=slip_cfg,
        lat_cfg=lat_cfg
    )

    # === ESTRATEGÍA ===
    strategy_module = load_strategy(strategy_path)
    if not hasattr(strategy_module, "Strategy"):
        raise AttributeError("Tu archivo de estrategia debe tener una clase llamada 'Strategy'")

    pass_params = pass_params or {}
    StrategyClass = strategy_module.Strategy
    cerebro.addstrategy(StrategyClass, **pass_params)

    # === ANALYZER NETO ===
    cerebro.addanalyzer(FeesNetAnalyzer, fees_cfg=fees_cfg, _name="feesnet")

    print(f"💰 Starting Portfolio Value: {cerebro.broker.getvalue():.2f}")

    # === RUN ===
    results = cerebro.run()
    strat = results[0]

    # === GUARDAR LOG NETO ===
    rows = strat.analyzers.feesnet.get_analysis()
    if rows:
        os.makedirs("results", exist_ok=True)
        out = f"results/{symbol}_{strategy_name}_net.csv"
        pd.DataFrame(rows).to_csv(out, index=False)
        print(f"🧾 Log NETO guardado en: {out}")

    # === FINAL VALUE ===
    print(f"🏁 Final Portfolio Value: {cerebro.broker.getvalue():.2f}")

    # === BUY & HOLD ===
    try:
        bh_final, bh_pct, bh_first, bh_last = compute_buy_and_hold(data_path, capital)

        print(
            f"📦 Buy & Hold BRUTO ({symbol}): "
            f"{capital:.2f} → {bh_final:.2f} ({bh_pct:+.2f}%) "
            f"[{bh_first:.4f} → {bh_last:.4f}]"
        )

        # BUY & HOLD NETO
        bh_fee_in, bh_fee_out, bh_fee_total = buyhold_fees(capital, bh_first, bh_last, fees_cfg)
        bh_final_net = bh_final - bh_fee_total
        bh_pct_net = (bh_final_net / capital - 1.0) * 100

        print(
            f"📦 Buy & Hold NETO: {capital:.2f} → {bh_final_net:.2f} "
            f"({bh_pct_net:+.2f}%)  [fees={bh_fee_total:.2f}]"
        )

    except Exception as e:
        print(f"⚠️ No se pudo calcular Buy&Hold: {e}")

    # === TRADE LOG ANTIGUO (opcional) ===
    if hasattr(strat, "trade_log") and len(strat.trade_log) > 0:
        df = pd.DataFrame(strat.trade_log)
        os.makedirs("results", exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = f"results/{symbol}_{strategy_name}_{timestamp}.csv"

        df.to_csv(out_path, index=False)
        print(f"📊 Resultados guardados en: {out_path}")
    else:
        print("⚠️ La estrategia no generó 'trade_log' o no hubo operaciones.")

    # === PLOT ===
    try:
        cerebro.plot(style="candlestick")
    except Exception:
        print("⚠️ No se pudo mostrar el gráfico.")



# ------------------------------------------------------
# MAIN
# ------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Ejecuta un backtest con Backtrader")
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--account", type=float, default=None)
    parser.add_argument("--split", type=str, default=None)
    parser.add_argument("--commission", type=float, default=0.001)
    args = parser.parse_args()

    # 1) Lista de símbolos
    symbols = [s.strip().upper() for s in args.symbols.split(",")]

    # === BLOQUE PASO 3.2: CONFIGURACIÓN GLOBAL ===
    ex_rules = ensure_exchange_rules(symbols)
    fees_cfg = default_fees_cfg()
    slip_cfg = default_slippage_cfg()
    lat_cfg  = default_latency_cfg()

    strategy_name = args.strategy
    data_dir = "data"

    # 3) Capital asignado
    capitals = {} 

    pass_params = {
        "max_alloc_pct": 0.85,
        "onramp_max": 5,
        "onramp_risk_cap": 0.0075
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
        raise SystemExit("Debes pasar --account o --capital")

    # 4) Ejecutar por símbolo
    for symbol in symbols:
        print("\n" + "=" * 70)
        print(f"🚀 {symbol} | estrategia {strategy_name} | capital asignado: {capitals[symbol]:.2f}")
        print("=" * 70)

        data_path = os.path.join(data_dir, f"{symbol}_4h.csv")
        strategy_path = os.path.join("strategies", f"{strategy_name}.py")

        if not os.path.exists(data_path):
            print(f"❌ CSV no encontrado: {data_path}")
            continue

        pass_params_sym = dict(pass_params)

        run_backtest(
            data_path, strategy_path, capitals[symbol],
            args.commission, symbol, strategy_name,
            pass_params=pass_params_sym,
            ex_rules=ex_rules,
            fees_cfg=fees_cfg,
            slip_cfg=slip_cfg,
            lat_cfg=lat_cfg
        )


if __name__ == "__main__":
    main()
