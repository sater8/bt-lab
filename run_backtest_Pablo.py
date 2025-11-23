# run_backtest_Pablo.py
import argparse
import importlib.util
import os
import sys
from datetime import datetime

import backtrader as bt
import pandas as pd

# --- Añadimos src al path ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, "src")
sys.path.append(SRC_DIR)

from tools.exchange_rules import ensure_exchange_rules
from tools.fees import default_fees_cfg
from tools.exec_middleware import enable_exec_middleware, FeesNetAnalyzer
from tools.slippage import default_slippage_cfg, default_latency_cfg

from tools.data_loader import detect_timeframe_and_compression
from tools.monthly_deposit import MonthlyDeposit


def import_strategy(strategy_path: str):
    """
    strategy_path: ruta relativa dentro de la RAÍZ del proyecto, por ejemplo:
        strategies/boll_breakout.py:Strategy

    Si no se indica el nombre de la clase (sin ":Clase"), intenta
    encontrar la primera subclase de bt.Strategy en el módulo.
    """
    if ":" in strategy_path:
        module_rel, class_name = strategy_path.split(":")
    else:
        module_rel, class_name = strategy_path, None

    module_path = os.path.join(BASE_DIR, module_rel.replace("/", os.sep))
    module_name = "user_strategy"

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)  # type: ignore

    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore

    if class_name is None:
        for attr in dir(module):
            obj = getattr(module, attr)
            if isinstance(obj, type) and issubclass(obj, bt.Strategy):
                return obj
        raise ValueError("No se encontró ninguna clase Strategy en el módulo")

    return getattr(module, class_name)


def get_strategy_label(strategy_path: str) -> str:
    """
    'strategies/boll_breakout.py:Strategy' → 'boll_breakout'
    """
    file_part = strategy_path.split(":")[0]
    base = os.path.basename(file_part)
    strategy_label = os.path.splitext(base)[0]
    return strategy_label


def parse_args():
    p = argparse.ArgumentParser(description="Pablo backtest runner simplificado")

    p.add_argument("--symbols", type=str, required=True,
                   help="Símbolos separados por coma, ej: BTCUSDT,ETHUSDT")
    p.add_argument("--strategy", type=str, required=True,
                   help="Ruta de la estrategia desde la raíz del proyecto, ej: "
                        "strategies/boll_breakout.py:Strategy")

    p.add_argument("--capital", type=float, required=True,
                   help="Capital inicial en moneda (ej. 6000)")
    p.add_argument("--commission", type=float, required=True,
                   help="Fee de Binance, ej. 0.001 = 0.1%%")

    p.add_argument("--data-dir", type=str,
                   default=os.path.join(BASE_DIR, "data"),
                   help="Carpeta donde están los CSV de datos")

    # DCA mensual (añadir capital)
    p.add_argument("--monthly-deposit", type=float, default=0.0,
                   help="Cantidad a añadir al inicio de cada mes (0 = sin DCA)")

    # Sizing (se pasarán a la estrategia; si usa PabloSizingMixin, los entiende)
    p.add_argument("--sizing-mode", type=str, default="all_in",
                   choices=["all_in", "fixed", "percent"],
                   help="Modo de sizing: all_in, fixed, percent")
    p.add_argument("--fixed-stake", type=float, default=0.0,
                   help="Cantidad fija por trade (solo si sizing-mode=fixed)")
    p.add_argument("--stake-pct", type=float, default=1.0,
                   help="Porcentaje del cash (0-1) si sizing-mode=percent")

    p.add_argument(
        "--plot",
        action="store_true",
        help="Mostrar gráfica con entradas y salidas al finalizar el backtest"
    )

    return p.parse_args()


def run_backtest_for_symbol(
    symbol: str,
    strategy_cls,
    strategy_label: str,
    args,
    ex_rules
):
    print("\n" + "=" * 70)
    print(f"🚀 {symbol} | estrategia {strategy_cls.__name__} | capital inicial: {args.capital:.2f}")
    print("=" * 70)

    cerebro = bt.Cerebro()

    # Evitar problemas de capital exactamente 0 en Backtrader
    user_start_capital = float(args.capital)
    initial_broker_cash = user_start_capital if user_start_capital > 0 else 1e-8
    cerebro.broker.setcash(initial_broker_cash)

    # Comisión de Backtrader a 0; usamos nuestro propio sistema de fees
    cerebro.broker.setcommission(commission=0.0)

    # --- DATA ---
    data_path = os.path.join(args.data_dir, f"{symbol}_4h.csv")
    if not os.path.isfile(data_path):
        raise FileNotFoundError(f"No se encontró el CSV para {symbol}: {data_path}")

    df = pd.read_csv(data_path)
    timeframe, compression = detect_timeframe_and_compression(df)

    data = bt.feeds.GenericCSVData(
        dataname=data_path,
        dtformat="%Y-%m-%d %H:%M:%S",
        timeframe=timeframe,
        compression=compression,
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

    # --- EXCHANGE RULES + MIDDLEWARE ---
    fees_cfg = default_fees_cfg(args.commission)
    slip_cfg = default_slippage_cfg()
    lat_cfg = default_latency_cfg()

    enable_exec_middleware(
        cerebro,
        ex_rules=ex_rules,
        fees_cfg=fees_cfg,
        max_alloc_pct=1.0,
        slip_cfg=slip_cfg,
        lat_cfg=lat_cfg,
    )

    # Analyzer de fees/trades
    cerebro.addanalyzer(FeesNetAnalyzer, _name='fees_net', fees_cfg=fees_cfg)

    # DCA mensual (opcional)
    if args.monthly_deposit > 0:
        cerebro.addanalyzer(MonthlyDeposit, _name='monthly_dep', amount=args.monthly_deposit)

    # Estrategia
    cerebro.addstrategy(
        strategy_cls,
        sizing_mode=args.sizing_mode,
        fixed_stake=args.fixed_stake,
        stake_pct=args.stake_pct,
    )

    # --- RUN ---
    results = cerebro.run()
    strat = results[0]

    # Etiquetas para los nombres de archivo
    mode_tag = args.sizing_mode            # all_in / fixed / percent
    dca_tag = "DCA" if args.monthly_deposit > 0 else "noDCA"

    # --- Log de TRADES desde el analyzer FeesNetAnalyzer ---
    trades = []
    analyzer = None
    try:
        analyzer = strat.analyzers.fees_net
        trades = analyzer.get_analysis()
    except AttributeError:
        analyzer = None
        trades = []

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # --- Totales de fees sacados directamente del analyzer (sirve aunque no haya ventas) ---
    if analyzer is not None:
        total_fee_in = float(getattr(analyzer, "total_fee_in", 0.0))
        total_fee_out = float(getattr(analyzer, "total_fee_out", 0.0))
    else:
        total_fee_in = 0.0
        total_fee_out = 0.0
    total_fees = total_fee_in + total_fee_out
    total_pnl_net = 0.0  # si quieres, se puede recomputar a partir de trades más adelante

    # --- Si hay trades, guardamos CSV de trades ---
    if trades:
        os.makedirs("results", exist_ok=True)
        trades_path = os.path.join(
            "results",
            f"{symbol}_{strategy_label}_{mode_tag}_{dca_tag}_trades_{timestamp}.csv"
        )
        df_trades = pd.DataFrame(trades)
        df_trades.to_csv(trades_path, index=False, encoding="utf-8-sig")
        print(f"🧾 Log de TRADES guardado en: {os.path.abspath(trades_path)}")

    # --- Totales de fees sacados DIRECTAMENTE del analyzer ---
    if analyzer is not None:
        total_fee_in = float(getattr(analyzer, "total_fee_in", 0.0))
        total_fee_out = float(getattr(analyzer, "total_fee_out", 0.0))
    else:
        total_fee_in = 0.0
        total_fee_out = 0.0

    total_fees = total_fee_in + total_fee_out

    # --- Valores del broker (BRUTOS, sin fees) ---
    final_value_gross = cerebro.broker.getvalue()
    final_cash_gross = cerebro.broker.getcash()

    # Info del DCA mensual
    if args.monthly_deposit > 0 and hasattr(strat.analyzers, 'monthly_dep'):
        monthly_info = strat.analyzers.monthly_dep.get_analysis()
        total_deposited = monthly_info.get('total_deposited', 0.0)
    else:
        total_deposited = 0.0

    starting_capital = user_start_capital
    total_invested = starting_capital + total_deposited

    # --- PnL BRUTO (sin considerar comisiones) ---
    pnl_abs_gross = final_value_gross - total_invested
    pnl_pct_gross = (pnl_abs_gross / total_invested) * 100 if total_invested > 0 else 0.0

    # --- Ajuste NETO restando todas las comisiones ---
    final_value_net = final_value_gross - total_fees
    pnl_abs_net = final_value_net - total_invested
    pnl_pct_net = (pnl_abs_net / total_invested) * 100 if total_invested > 0 else 0.0

    # --- PRINT RESUMEN EN CONSOLA ---
    print(f"💰 Capital inicial: {starting_capital:.2f}")
    if total_deposited > 0:
        print(f"➕ Total depositado vía DCA mensual: {total_deposited:.2f}")
    print(f"💰 Total invertido (inicio + DCA): {total_invested:.2f}")

    print(f"🏁 Final Portfolio Value BRUTO (sin fees): {final_value_gross:.2f}")
    print(f"🏁 Final Portfolio Value NETO  (con fees): {final_value_net:.2f}")

    print(f"📉 PnL BRUTO: {pnl_abs_gross:.2f} ({pnl_pct_gross:.2f}%)")
    print(f"📉 PnL NETO : {pnl_abs_net:.2f} ({pnl_pct_net:.2f}%)")

    print(f"💸 Fees totales: {total_fees:.2f} (entrada {total_fee_in:.2f} + salida {total_fee_out:.2f})")
    print(f"💵 Cash final (bruto, broker): {final_cash_gross:.2f}")

    # Mostrar gráfica si el usuario lo pide
    if getattr(args, "plot", False):
        cerebro.plot(style='candlestick')

    # --- LOG NET (resumen portfolio) ---
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = os.path.join(BASE_DIR, "results")
    os.makedirs(results_dir, exist_ok=True)

    net_path = os.path.join(
        results_dir,
        f"{symbol}_{strategy_label}_{mode_tag}_{dca_tag}_net_{ts}.csv"
    )

    import csv
    with open(net_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "symbol",
            "strategy",
            "starting_capital",
            "total_deposited",
            "total_invested",
            "final_value_gross",
            "final_value_net",
            "pnl_abs_gross",
            "pnl_pct_gross",
            "pnl_abs_net",
            "pnl_pct_net",
            "final_cash_gross",
            "total_fee_in",
            "total_fee_out",
            "total_fees",
        ])
        writer.writerow([
            symbol,
            strategy_cls.__name__,
            f"{starting_capital:.8f}",
            f"{total_deposited:.8f}",
            f"{total_invested:.8f}",
            f"{final_value_gross:.8f}",
            f"{final_value_net:.8f}",
            f"{pnl_abs_gross:.8f}",
            f"{pnl_pct_gross:.4f}",
            f"{pnl_abs_net:.8f}",
            f"{pnl_pct_net:.4f}",
            f"{final_cash_gross:.8f}",
            f"{total_fee_in:.8f}",
            f"{total_fee_out:.8f}",
            f"{total_fees:.8f}",
        ])

    print(f"🧾 Log NET (resumen portfolio) guardado en: {net_path}")


def main():
    args = parse_args()
    strategy_cls = import_strategy(args.strategy)
    strategy_label = get_strategy_label(args.strategy)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    ex_rules = ensure_exchange_rules(symbols)

    for symbol in symbols:
        run_backtest_for_symbol(symbol, strategy_cls, strategy_label, args, ex_rules)


if __name__ == "__main__":
    main()
