# -*- coding: utf-8 -*-
# src/tools/fees.py
# Configuración y utilidades para comisiones (fees)

def default_fees_cfg(commission: float | None = None):
    """
    Devuelve la configuración por defecto de fees simulados.

    commission:
        - Si es None → usa los valores "clásicos" (0.0002 / 0.0004).
        - Si viene un float (por ejemplo 0.001) → se usa para maker/taker
          y también para buy&hold, para que sea consistente con el CLI.
    """
    if commission is None:
        maker = 0.001    # 0.1%
        taker = 0.001    # 0.1%
    else:
        maker = float(commission)
        taker = float(commission)

    return {
        "maker": maker,
        "taker": taker,
        "buyhold_in": taker,
        "buyhold_out": taker,
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
