# -*- coding: utf-8 -*-
# src/tools/exchange_rules.py
# Binance Spot: exchangeInfo (tickSize/stepSize/minNotional) + helpers para Backtrader.
import os, json, urllib.request, urllib.error
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Dict, Any, List, Tuple, Optional

CACHE_PATH = os.path.join("config", "exchange_info.json")
BASES = [
    "https://api.binance.com",
    "https://api-gcp.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api4.binance.com",
    "https://data-api.binance.vision",
]

def _http_get(url: str, timeout: float = 12.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")

def _fetch_exchange_info(symbols: List[str]) -> Dict[str, Any]:
    if len(symbols) == 1:
        qs = f"symbol={symbols[0]}"
    else:
        quoted = ",".join([f'%22{s}%22' for s in symbols])
        qs = f"symbols=[{quoted}]"
    last_err = None
    for base in BASES:
        try:
            body = _http_get(f"{base}/api/v3/exchangeInfo?{qs}")
            data = json.loads(body)
            out = {sym["symbol"]: sym for sym in data.get("symbols", [])}
            if out:
                return out
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"No pude obtener exchangeInfo ({symbols}). Último error: {last_err}")

def _ensure_cache_dir():
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)

def _load_cache() -> Dict[str, Any]:
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def _save_cache(cache: Dict[str, Any]):
    _ensure_cache_dir()
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)

def ensure_exchange_rules(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    cache = _load_cache()

    EXTERNAL_SYMBOLS = ["XAUUSD", "XAGUSD", "GOLD", "OIL", "SP500"]
    need = [s for s in symbols if s not in cache and s not in EXTERNAL_SYMBOLS]

    if need:
        fetched = _fetch_exchange_info(need)
        cache.update(fetched)
        _save_cache(cache)

    parsed = {}
    for s in symbols:
        if s in cache:
            parsed[s] = _parse_symbol_filters(cache[s])

    # >>> NUEVO BLOQUE PARA REGLAS GENÉRICAS <<<
    for s in symbols:
        if s not in parsed:   # símbolo externo (oro, índices…)
            parsed[s] = {
                "symbol": s,
                "base": s,
                "quote": "USD",
                "minPrice": Decimal("0"),
                "maxPrice": Decimal("9999999"),
                "tickSize": Decimal("0.01"),
                "minQty": Decimal("0.0001"),
                "maxQty": Decimal("9999999"),
                "stepSize": Decimal("0.01"),
                "minQtyMkt": Decimal("0.0001"),
                "maxQtyMkt": Decimal("9999999"),
                "stepSizeMkt": Decimal("0.01"),
                "minNotional": Decimal("0"),
                "applyToMarket": True,
            }

    return parsed


def _get_filter(sym: Dict[str, Any], t: str) -> Optional[Dict[str, str]]:
    for f in sym.get("filters", []):
        if f.get("filterType") == t:
            return f
    return None

def _D(x) -> Decimal:
    return Decimal(str(x))

def _parse_symbol_filters(sym_json: Dict[str, Any]) -> Dict[str, Any]:
    price_f = _get_filter(sym_json, "PRICE_FILTER") or {}
    lot_f   = _get_filter(sym_json, "LOT_SIZE") or {}
    mlot_f  = _get_filter(sym_json, "MARKET_LOT_SIZE") or {}
    min_not = _get_filter(sym_json, "NOTIONAL") or _get_filter(sym_json, "MIN_NOTIONAL") or {}
    return {
        "symbol": sym_json.get("symbol"),
        "base": sym_json.get("baseAsset"), "quote": sym_json.get("quoteAsset"),
        "minPrice": _D(price_f.get("minPrice", "0")),
        "maxPrice": _D(price_f.get("maxPrice", "0")),
        "tickSize": _D(price_f.get("tickSize", "0.00000001") or "0.00000001"),
        "minQty":   _D(lot_f.get("minQty", "0")), "maxQty": _D(lot_f.get("maxQty", "0")),
        "stepSize": _D(lot_f.get("stepSize", "1")),
        "minQtyMkt":   _D(mlot_f.get("minQty", lot_f.get("minQty","0"))),
        "maxQtyMkt":   _D(mlot_f.get("maxQty", lot_f.get("maxQty","0"))),
        "stepSizeMkt": _D(mlot_f.get("stepSize", lot_f.get("stepSize","1"))),
        "minNotional": _D(min_not.get("minNotional", min_not.get("minNotionalLocal","0") or "0")),
        "applyToMarket": str(min_not.get("applyToMarket","true")).lower() == "true",
    }

def _round_down_to_step(x: Decimal, step: Decimal) -> Decimal:
    if step <= 0: return x
    q = (x / step).to_integral_value(rounding=ROUND_DOWN)
    return q * step

def _round_price(px: Decimal, tick: Decimal, side: str) -> Decimal:
    if tick <= 0: return px
    if side.lower()=="buy":
        return _round_down_to_step(px, tick)
    # SELL: aproxima al múltiplo más cercano hacia arriba (suavizado)
    q = (px / tick).to_integral_value(rounding=ROUND_HALF_UP)
    return q * tick

def conform_order(
    rules: Dict[str, Any],
    side: str,                 # "buy" / "sell"
    order_type: str,           # "MARKET" | "LIMIT"
    ref_price: float,          # precio de referencia (close si MARKET)
    qty: float                 # cantidad propuesta (BASE)
) -> Tuple[bool, Dict[str, Any]]:
    """
    Ajusta qty/precio a tick/step y asegura minNotional si es posible.
    Devuelve (ok, payload) con 'price','qty','notional' ajustados o razón de fallo.
    """
    tick = rules["tickSize"]
    if order_type == "LIMIT":
        step = rules["stepSize"];   minq = rules["minQty"];   maxq = rules["maxQty"]
    else:
        step = rules["stepSizeMkt"]; minq = rules["minQtyMkt"]; maxq = rules["maxQtyMkt"]

    px = _D(ref_price)
    q  = _D(qty)
    px = _round_price(px, tick, side)
    q  = _round_down_to_step(q, step)

    if minq > 0 and q < minq:
        q = minq  # subimos a mínimo de cantidad

    if maxq > 0 and q > maxq:
        q = maxq  # clamp

    notional = px * q
    if rules["minNotional"] > 0 and notional < rules["minNotional"]:
        need_q = (rules["minNotional"] / (px if px > 0 else _D("1")))
        need_q = _round_down_to_step(need_q, step) if order_type=="LIMIT" else need_q.quantize(step)
        if need_q <= 0:
            return False, {"reason": "minNotional_unreachable"}
        q = need_q
        notional = px * q

    return True, {"price": float(px), "qty": float(q), "notional": float(notional)}

# === Helper específico para Backtrader (MARKET) =========================
def bt_conform_market_order(
    rules: Dict[str, Any],
    side: str,
    ref_price: float,
    qty: float,
    cap_notional: float
) -> Tuple[bool, float, float, str]:
    """
    Ajusta qty a stepSizeMkt y cumple minNotional y tope 'cap_notional'.
    Devuelve (ok, qty_adj, notional_adj, reason).
    """
    ok, adj = conform_order(rules, side=side, order_type="MARKET", ref_price=ref_price, qty=qty)
    if not ok:
        return False, 0.0, 0.0, adj.get("reason","invalid")
    if adj["notional"] > cap_notional > 0:
        # no forzamos a bajar qty por debajo de minNotional: si excede el cap, saltamos entrada
        return False, 0.0, 0.0, "excede_cap_nueva_orden"
    return True, adj["qty"], adj["notional"], ""