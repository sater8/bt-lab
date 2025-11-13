# -*- coding: utf-8 -*-
# src/tools/fees.py
# Configuración y utilidades para comisiones (fees)

def default_fees_cfg():
    """
    Devuelve la configuración por defecto de fees simulados.
    """
    return {
        "maker": 0.0002,   # 0.02%
        "taker": 0.0004,   # 0.04%
        "buyhold_in": 0.0004,   # Usado en comparación Buy&Hold
        "buyhold_out": 0.0004
    }


def fee_amount(notional: float, fee_type: str, cfg: dict):
    """
    Calcula la comisión para un notional basado en el tipo (maker/taker).
    """
    fee_pct = cfg.get(fee_type, 0.0)
    return float(notional) * float(fee_pct)


def buyhold_fees(capital: float, px_in: float, px_out: float, cfg: dict):
    """
    Calcula los fees totales en un Buy&Hold para comparación justa.
    capital : inversión inicial
    px_in   : precio de compra
    px_out  : precio de venta
    """
    qty = capital / px_in
    notion_in = capital
    notion_out = qty * px_out

    fee_in = notion_in * cfg.get("buyhold_in", 0.0)
    fee_out = notion_out * cfg.get("buyhold_out", 0.0)

    return fee_in, fee_out, (fee_in + fee_out)
