"""4-Layer Signal Scoring System → confidence + grade.

Plan layers (Master Plan v6.0):
  Layer 1 — MTF Alignment        max 45 pts
  Layer 2 — SMC/ICT context      max 30 pts (+ bonuses)
  Layer 3 — Candle Reaction      max 25 pts (+ bonuses)
  Layer 4 — AI Ensemble          max 30 pts (+ bonus)
  FINAL = total / 160 (per plan, leaves headroom for bonuses)
"""

from config import GRADE_ELITE, GRADE_HIGH, GRADE_MODERATE


def score_to_confidence(
    mtf_pts:      float,
    smc_pts:      float,
    reaction_pts: float,
    ai_pts:       float,
    meta_pass:    bool,
    adx:          float,
    kalman_agree: bool,
    kalman_vel:   float,
) -> float:
    """Combine 4 scoring layers into a final confidence [0, 1]."""
    raw_total = mtf_pts + smc_pts + reaction_pts + ai_pts
    max_pts   = 160.0  # per plan v6.0 (was 130 — allows bonus headroom)

    confidence = raw_total / max_pts

    # Meta-label boost
    if meta_pass:
        confidence += 0.04

    # Choppy market penalty
    if adx < 15:
        confidence *= 0.55

    # Kalman noise filter — uses ABS velocity (negative trend has |vel|>0)
    if abs(kalman_vel) < 1e-5:
        confidence *= 0.70

    # Kalman trend agreement
    if kalman_agree:
        confidence += 0.02
    else:
        confidence -= 0.03

    return float(min(max(confidence, 0.0), 1.0))


def smc_score_pts(
    smc: dict[str, object],
    inst: dict[str, float],
    wyckoff: dict[str, int],
    harmonics: dict[str, int],
    sd: dict[str, float],
    elliott: dict[str, float],
    direction: str = "GREEN",
) -> float:
    """SMC/ICT context layer (max ~30 pts, bonuses allowed). Direction-aware."""
    pts = 0.0
    bull = direction == "GREEN"

    # Order Blocks — aligned gives pts, opposing penalises
    if bull and smc.get("price_at_bullish_ob"):
        pts += 10
    elif not bull and smc.get("price_at_bearish_ob"):
        pts += 10
    elif bull and smc.get("price_at_bearish_ob"):
        pts -= 5
    elif not bull and smc.get("price_at_bullish_ob"):
        pts -= 5

    # FVG
    if bull and smc.get("price_in_bullish_fvg"):
        pts += 8
    elif not bull and smc.get("price_in_bearish_fvg"):
        pts += 8
    elif bull and smc.get("price_in_bearish_fvg"):
        pts -= 3
    elif not bull and smc.get("price_in_bullish_fvg"):
        pts -= 3

    # BOS
    if bull and smc.get("bullish_bos"):
        pts += 7
    elif not bull and smc.get("bearish_bos"):
        pts += 7
    elif bull and smc.get("bearish_bos"):
        pts -= 4
    elif not bull and smc.get("bullish_bos"):
        pts -= 4

    # OTE zone — direction-aware fields take priority
    if bull and smc.get("in_ote_zone_bull"):
        pts += 5
    elif not bull and smc.get("in_ote_zone_bear"):
        pts += 5
    elif smc.get("in_ote_zone"):
        pts += 3  # legacy fallback

    # Liquidity
    if bull and smc.get("sell_side_liquidity"):
        pts += 5
    elif not bull and smc.get("buy_side_liquidity"):
        pts += 5
    if smc.get("liquidity_swept"):
        pts += 8

    # Institutional
    if bull and (inst.get("judas_swing_bull") or inst.get("mm_bull_cycle")):
        pts += 6
    elif not bull and (inst.get("judas_swing_bear") or inst.get("mm_bear_cycle")):
        pts += 6
    if bull and (inst.get("bullish_liquidity_sweep") or inst.get("displacement_bull")):
        pts += 5
    elif not bull and (inst.get("bearish_liquidity_sweep") or inst.get("displacement_bear")):
        pts += 5

    # Wyckoff
    if bull and wyckoff.get("wyckoff_spring"):
        pts += 8
    elif not bull and wyckoff.get("wyckoff_upthrust"):
        pts += 8
    if bull and wyckoff.get("wyckoff_sos"):
        pts += 6
    elif not bull and wyckoff.get("wyckoff_sow"):
        pts += 6

    # Harmonics — direction aware
    bull_harm = {"gartley_bull", "bat_bull", "butterfly_bull", "crab_bull"}
    bear_harm = {"gartley_bear", "bat_bear", "butterfly_bear", "crab_bear"}
    if bull and any(harmonics.get(p, 0) for p in bull_harm):
        pts += 8
    elif not bull and any(harmonics.get(p, 0) for p in bear_harm):
        pts += 8

    # S/D zones
    if sd.get("sd_zone_fresh"):
        if bull and sd.get("sd_at_demand"):
            pts += 6
        elif not bull and sd.get("sd_at_supply"):
            pts += 6

    # Elliott waves
    if bull and elliott.get("elliott_impulse_bull"):
        pts += 7
    elif not bull and elliott.get("elliott_impulse_bear"):
        pts += 7

    return pts


def reaction_score_pts(reaction: dict[str, float]) -> float:
    """Candle reaction layer (max 25 + bonuses)."""
    net = float(reaction.get("net_score", 0))
    if net > 60:
        pts = 25.0
    elif net > 40:
        pts = 20.0
    elif net > 20:
        pts = 14.0
    elif net > 0:
        pts = 8.0
    else:
        pts = 0.0

    if reaction.get("pin_bar"):
        pts += 5
    if reaction.get("engulfing"):
        pts += 6

    return pts


def ai_score_pts(ai_confidence: float, meta_prob: float) -> float:
    """AI ensemble layer (max 30 + bonus)."""
    if ai_confidence >= 0.80:
        pts = 30.0
    elif ai_confidence >= 0.75:
        pts = 25.0
    elif ai_confidence >= 0.70:
        pts = 20.0
    elif ai_confidence >= 0.65:
        pts = 14.0
    elif ai_confidence >= 0.60:
        pts = 8.0
    else:
        pts = 0.0

    if meta_prob >= 0.75:
        pts += 5

    return pts


def grade(confidence: float) -> str:
    if confidence >= GRADE_ELITE:
        return "ELITE"
    if confidence >= GRADE_HIGH:
        return "HIGH"
    if confidence >= GRADE_MODERATE:
        return "MODERATE"
    return "SKIP"


def consensus_check(
    feat: dict[str, object],
    direction: str,
    pattern_present: bool = False,
) -> int:
    """8-check consensus filter per plan. Returns score 0-8.

    GREEN: RSI<65, MACD>0, BB<0.75, Stoch<80, EMA5>EMA20, ADX>20, Pattern✓, SMC✓
    RED:   inverted thresholds.
    """
    score = 0
    is_bull = direction == "GREEN"

    rsi14 = float(feat.get("rsi_14", 50))
    macd_h = float(feat.get("macd_hist", 0))
    bb_pos = float(feat.get("bb_pos", 0.5))
    stoch_k = float(feat.get("stoch_k", 50))
    ema5  = float(feat.get("ema_5", 0))
    ema20 = float(feat.get("ema_20", 0))
    adx   = float(feat.get("adx_14", 0))
    net   = float(feat.get("smc_net_score", 0))

    if is_bull:
        score += int(rsi14 < 65)
        score += int(macd_h > 0)
        score += int(bb_pos < 0.75)
        score += int(stoch_k < 80)
        score += int(ema5 > ema20)
        score += int(adx > 20)
        score += int(net > 0)
        score += int(pattern_present)
    else:
        score += int(rsi14 > 35)
        score += int(macd_h < 0)
        score += int(bb_pos > 0.25)
        score += int(stoch_k > 20)
        score += int(ema5 < ema20)
        score += int(adx > 20)
        score += int(net < 0)
        score += int(pattern_present)

    return score


def apply_consensus_penalty(confidence: float, consensus_score: int) -> float:
    if consensus_score >= 7:
        return min(confidence + 0.04, 1.0)
    if consensus_score < 5:
        return confidence * (0.5 + 0.5 * consensus_score / 5)
    return confidence
