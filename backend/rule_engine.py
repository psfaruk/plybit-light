"""Deterministic Rule Engine (Model 16) — SMC/ICT hard rules."""

import pandas as pd
import numpy as np


def rule_engine_predict(
    df: pd.DataFrame,
    smc: dict[str, object],
    inst: dict[str, float],
    reaction: dict[str, float],
    utc_hour: int = 0,
) -> dict[str, object]:
    """Returns direction + confidence based on hard rules."""
    if len(df) < 10:
        return {"direction": "SKIP", "confidence": 0.0, "rules_fired": []}

    c = df["close"]
    o = df["open"]
    h = df["high"]
    l = df["low"]

    bull_score  = 0.0
    bear_score  = 0.0
    rules_fired: list[str] = []

    # ── Killzone bonus ────────────────────────────────────────
    in_kz = utc_hour in range(7, 10) or utc_hour in range(12, 16)
    kz_w  = 1.3 if in_kz else 1.0

    # ── Rule: Bullish OB + BOS ────────────────────────────────
    if smc.get("price_at_bullish_ob") and smc.get("bullish_bos"):
        bull_score += 20 * kz_w
        rules_fired.append("bull_ob_bos")

    # ── Rule: Bearish OB + BOS ────────────────────────────────
    if smc.get("price_at_bearish_ob") and smc.get("bearish_bos"):
        bear_score += 20 * kz_w
        rules_fired.append("bear_ob_bos")

    # ── Rule: FVG entry ───────────────────────────────────────
    if smc.get("price_in_bullish_fvg"):
        bull_score += 15 * kz_w
        rules_fired.append("bull_fvg_entry")
    if smc.get("price_in_bearish_fvg"):
        bear_score += 15 * kz_w
        rules_fired.append("bear_fvg_entry")

    # ── Rule: OTE zone ───────────────────────────────────────
    if smc.get("in_ote_zone"):
        trend = smc.get("trend", "range")
        if trend == "up":
            bull_score += 10
            rules_fired.append("ote_bull")
        elif trend == "down":
            bear_score += 10
            rules_fired.append("ote_bear")

    # ── Rule: Liquidity Sweep reversal ───────────────────────
    if inst.get("bullish_liquidity_sweep"):
        bull_score += 18 * kz_w
        rules_fired.append("bull_liq_sweep")
    if inst.get("bearish_liquidity_sweep"):
        bear_score += 18 * kz_w
        rules_fired.append("bear_liq_sweep")

    # ── Rule: Judas Swing ────────────────────────────────────
    if inst.get("judas_swing_bull"):
        bull_score += 15
        rules_fired.append("judas_bull")
    if inst.get("judas_swing_bear"):
        bear_score += 15
        rules_fired.append("judas_bear")

    # ── Rule: Strong candle reaction ─────────────────────────
    net = float(reaction.get("net_score", 0))
    if net > 40:
        bull_score += min(net * 0.3, 15)
        rules_fired.append("strong_bull_reaction")
    elif net < -40:
        bear_score += min(abs(net) * 0.3, 15)
        rules_fired.append("strong_bear_reaction")

    # ── Rule: Pin bar at key zone ─────────────────────────────
    if reaction.get("pin_bull") and (smc.get("price_at_bullish_ob") or smc.get("price_in_bullish_fvg")):
        bull_score += 12
        rules_fired.append("pin_bar_ob_bull")
    if reaction.get("pin_bear") and (smc.get("price_at_bearish_ob") or smc.get("price_in_bearish_fvg")):
        bear_score += 12
        rules_fired.append("pin_bar_ob_bear")

    # ── Rule: Engulfing candle ───────────────────────────────
    if reaction.get("bull_engulf"):
        bull_score += 10
        rules_fired.append("bull_engulf")
    if reaction.get("bear_engulf"):
        bear_score += 10
        rules_fired.append("bear_engulf")

    # ── Normalize to confidence ───────────────────────────────
    max_possible = 90.0
    if bull_score > bear_score and bull_score > 15:
        confidence = min(0.50 + (bull_score / max_possible) * 0.45, 0.95)
        return {"direction": "GREEN", "confidence": confidence, "rules_fired": rules_fired}
    elif bear_score > bull_score and bear_score > 15:
        confidence = min(0.50 + (bear_score / max_possible) * 0.45, 0.95)
        return {"direction": "RED", "confidence": confidence, "rules_fired": rules_fired}
    else:
        return {"direction": "SKIP", "confidence": 0.0, "rules_fired": rules_fired}
