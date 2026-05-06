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

    # ── Rule: OTE zone (direction-aware) ────────────────────
    if smc.get("in_ote_zone_bull"):
        bull_score += 10
        rules_fired.append("ote_bull")
    if smc.get("in_ote_zone_bear"):
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

    # ── Rule: Liquidity sweep reversal (directional fake sweep) ──
    if smc.get("liq_sweep_bull"):
        bull_score += 20 * kz_w
        rules_fired.append("liq_sweep_bull")
    if smc.get("liq_sweep_bear"):
        bear_score += 20 * kz_w
        rules_fired.append("liq_sweep_bear")

    # ── Rule: PD Array — multiple confluences at entry zone ──
    pd_bull = (
        int(bool(smc.get("price_at_bullish_ob")))   * 3 +
        int(bool(smc.get("price_in_bullish_fvg")))  * 2 +
        int(bool(smc.get("ssl_swept") or inst.get("bullish_liquidity_sweep"))) * 3 +
        int(bool(smc.get("in_ote_zone_bull")))      * 2 +
        int(bool(smc.get("in_discount_zone")))      * 1 +
        int(bool(smc.get("bullish_choch")))         * 2
    )
    pd_bear = (
        int(bool(smc.get("price_at_bearish_ob")))   * 3 +
        int(bool(smc.get("price_in_bearish_fvg")))  * 2 +
        int(bool(smc.get("bsl_swept") or inst.get("bearish_liquidity_sweep"))) * 3 +
        int(bool(smc.get("in_ote_zone_bear")))      * 2 +
        int(bool(smc.get("in_premium_zone")))       * 1 +
        int(bool(smc.get("bearish_choch")))         * 2
    )
    if pd_bull >= 5:
        bull_score += pd_bull * 2 * kz_w
        rules_fired.append(f"pd_array_bull_{pd_bull}")
    if pd_bear >= 5:
        bear_score += pd_bear * 2 * kz_w
        rules_fired.append(f"pd_array_bear_{pd_bear}")

    # ── Rule: SMC full setup (sweep + ChoCh + OB/FVG) ────────
    smc_full_bull = (
        (smc.get("ssl_swept") or inst.get("bullish_liquidity_sweep")) and
        smc.get("bullish_choch") and
        (smc.get("price_at_bullish_ob") or smc.get("price_in_bullish_fvg"))
    )
    smc_full_bear = (
        (smc.get("bsl_swept") or inst.get("bearish_liquidity_sweep")) and
        smc.get("bearish_choch") and
        (smc.get("price_at_bearish_ob") or smc.get("price_in_bearish_fvg"))
    )
    if smc_full_bull:
        bull_score += 35 * kz_w
        rules_fired.append("smc_full_bull")
    if smc_full_bear:
        bear_score += 35 * kz_w
        rules_fired.append("smc_full_bear")

    # ── Rule: HH/HL uptrend + key level ─────────────────────
    if smc.get("hh_hl_structure") and (smc.get("price_at_bullish_ob") or smc.get("in_ote_zone_bull") or smc.get("in_discount_zone")):
        bull_score += 12 * kz_w
        rules_fired.append("hh_hl_key_level")
    if smc.get("lh_ll_structure") and (smc.get("price_at_bearish_ob") or smc.get("in_ote_zone_bear") or smc.get("in_premium_zone")):
        bear_score += 12 * kz_w
        rules_fired.append("lh_ll_key_level")

    # ── Rule: Role reversal retest ────────────────────────────
    if smc.get("role_reversal_bull") and (reaction.get("pin_bull") or reaction.get("bull_engulf")):
        bull_score += 15 * kz_w
        rules_fired.append("role_reversal_bull_retest")
    if smc.get("role_reversal_bear") and (reaction.get("pin_bear") or reaction.get("bear_engulf")):
        bear_score += 15 * kz_w
        rules_fired.append("role_reversal_bear_retest")

    # ── Rule: Round number rejection ─────────────────────────
    if smc.get("round_number_near"):
        net_react = float(reaction.get("net_score", 0))
        if net_react > 30:
            bull_score += 8
            rules_fired.append("round_number_bull_reject")
        elif net_react < -30:
            bear_score += 8
            rules_fired.append("round_number_bear_reject")

    # ── Rule: Momentum confirmation (3+ consecutive candles) ─
    mc_bull = int(smc.get("momentum_bull_count", 0))
    mc_bear = int(smc.get("momentum_bear_count", 0))
    if mc_bull >= 3:
        bull_score += min(mc_bull * 2, 8)
        rules_fired.append(f"momentum_bull_{mc_bull}")
    if mc_bear >= 3:
        bear_score += min(mc_bear * 2, 8)
        rules_fired.append(f"momentum_bear_{mc_bear}")

    # ── Rule: Close in upper/lower third ────────────────────
    if reaction.get("close_upper_third") and bull_score > bear_score:
        bull_score += 6
        rules_fired.append("close_upper_third")
    if reaction.get("close_lower_third") and bear_score > bull_score:
        bear_score += 6
        rules_fired.append("close_lower_third")

    # ── Rule: Real breakout confirmation ─────────────────────
    if smc.get("breakout_real_bull"):
        bull_score += 10
        rules_fired.append("breakout_real_bull")
    if smc.get("breakout_real_bear"):
        bear_score += 10
        rules_fired.append("breakout_real_bear")

    # ── Rule: Consolidation — halve all scores (guide: ranging = no trade) ──
    if smc.get("consolidation"):
        bull_score *= 0.5
        bear_score *= 0.5
        rules_fired.append("consolidation_penalty")

    # ── Normalize to confidence ───────────────────────────────
    max_possible = 160.0
    if bull_score > bear_score and bull_score > 15:
        confidence = min(0.50 + (bull_score / max_possible) * 0.45, 0.95)
        return {"direction": "GREEN", "confidence": confidence, "rules_fired": rules_fired}
    elif bear_score > bull_score and bear_score > 15:
        confidence = min(0.50 + (bear_score / max_possible) * 0.45, 0.95)
        return {"direction": "RED", "confidence": confidence, "rules_fired": rules_fired}
    else:
        return {"direction": "SKIP", "confidence": 0.0, "rules_fired": rules_fired}
