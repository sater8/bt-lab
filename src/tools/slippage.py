# -*- coding: utf-8 -*-
# src/tools/slippage.py
# Slippage determinista en bps + modelo simple de latencias

from typing import Dict


def default_slippage_cfg() -> Dict:
    """
    Configuración base de slippage en bps.
    Los parámetros controlan cuánto pesan la volatilidad, el tamaño y la latencia.
    """
    return {
        "k_vol": 0.35,    # peso de la volatilidad (ATR)
        "k_size": 0.50,   # peso del tamaño relativo vs volumen
        "k_lat": 0.10,    # multiplicador extra por latencia
        "min_bps": 1.0,   # mínimo 1 bps
        "max_bps": 80.0   # máximo 80 bps
    }


def default_latency_cfg() -> Dict:
    """
    Latencias deterministas (ms). Puedes ajustar por exchange/infra.
    """
    return {
        "submit_ms": 120,
        "ack_ms": 80,
        "fill_ms": 200
    }


def _atr14_pct_from_data(data) -> float:
    """
    ATR14 % estimado desde la serie 4H (sin depender de indicadores externos).
    Devuelve un porcentaje (0..0.20) del precio.
    """
    n = min(len(data), 16)
    if n < 2:
        return 0.0

    # True Range última vela
    c1 = float(data.close[-2])
    hi = float(data.high[-1])
    lo = float(data.low[-1])
    cl = float(data.close[-1])

    tr_last = max(hi - lo, abs(hi - c1), abs(lo - c1))

    # Media RMA sobre 14 (aprox con media simple sobre últimas 14 si no hay histórico)
    tr_sum = 0.0
    m = 0
    for i in range(1, min(len(data), 15) + 1):
        if i + 1 <= len(data):
            c_prev = float(data.close[-i - 1])
        else:
            c_prev = float(data.close[-i])
        h = float(data.high[-i])
        l = float(data.low[-i])
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        tr_sum += tr
        m += 1

    atr = tr_sum / max(m, 1)
    price = max(cl, 1e-12)
    # cap a 20% por seguridad
    return min(max(atr / price, 0.0), 0.20)


def _spread_bps_from_bar(data) -> float:
    """
    Estimación de spread en bps a partir de la vela 4H actual (proxy).
    Usa el rango high-low como proxy de spread y lo acota.
    """
    hi = float(data.high[-1])
    lo = float(data.low[-1])
    if (hi + lo) > 0:
        mid = (hi + lo) / 2.0
    else:
        mid = float(data.close[-1])

    raw_bps = 1e4 * (hi - lo) / max(mid, 1e-12)
    # Los crypto majors suelen estar entre 2–20 bps en calma; acota:
    return float(min(max(raw_bps * 0.35, 2.0), 30.0))


def _size_vs_vol_4h(notional: float, data) -> float:
    """
    Tamaño relativo a la liquidez 4H: notional / (precio * volumen_base_4h).
    Devuelve un ratio 0..1 (cap a 100% del volumen de la vela).
    """
    px = float(data.close[-1])
    vol_base = float(getattr(data, "volume", [0])[-1] or 0.0)
    denom = max(px * vol_base, 1e-9)
    return float(min(max(notional / denom, 0.0), 1.0))  # cap 100%


def latency_ms_total(lat_cfg: Dict) -> int:
    """
    Suma simple de latencias de submit+ack+fill.
    """
    return int(
        max(0, lat_cfg.get("submit_ms", 0)) +
        max(0, lat_cfg.get("ack_ms", 0)) +
        max(0, lat_cfg.get("fill_ms", 0))
    )


def slip_bps(side: str,
             price: float,
             qty: float,
             data,
             lat_cfg: Dict,
             cfg: Dict) -> float:
    """
    Calcula el slippage total en bps para una orden de mercado.

    side: 'buy'/'sell'
    price, qty: de la orden (post-filtros exchange)
    data: feed 4H (para ohlc/vol)
    lat_cfg: dict de latencias ms (determinista)
    cfg: parámetros de slippage (k_vol, k_size, k_lat, min_bps, max_bps)
    """
    spread_bps = _spread_bps_from_bar(data)
    atr_pct = _atr14_pct_from_data(data)          # 0..0.20
    notional = price * qty
    sz_rel = _size_vs_vol_4h(notional, data)      # 0..1

    k_vol = float(cfg.get("k_vol", 0.35))
    k_size = float(cfg.get("k_size", 0.50))
    k_lat = float(cfg.get("k_lat", 0.10))
    lat_ms = latency_ms_total(lat_cfg)

    # base: media de spread
    base = 0.5 * spread_bps

    # extra por volatilidad y tamaño
    extra = k_vol * (atr_pct * 1e4) + k_size * (sz_rel * 1e4)

    # penalización por latencia: ms → segundos → escala por atr_pct (en bps)
    extra += k_lat * (lat_ms / 1000.0) * (atr_pct * 1e4)

    bps = base + extra
    bps = max(float(cfg.get("min_bps", 1.0)), min(bps, float(cfg.get("max_bps", 80.0))))
    return bps


def apply_slippage(side: str,
                   ref_price: float,
                   qty: float,
                   data,
                   lat_cfg: Dict,
                   cfg: Dict) -> float:
    """
    Devuelve el precio EFECTIVO con slippage aplicado.
    """
    s_bps = slip_bps(side, ref_price, qty, data, lat_cfg, cfg)
    if side.lower() == "buy":
        mult = 1.0 + s_bps / 1e4
    else:
        mult = 1.0 - s_bps / 1e4
    return ref_price * mult
