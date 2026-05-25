"""
Multi-Agent Consensus Engine  —  PLAYBIT AI

Agents (8 specialized analysts, each sees the others' work via SharedBrain):
  smc_agent          — SMC: BOS/CHOCH, Order Block, FVG, OTE, Liquidity
  ict_agent          — ICT: Kill Zones, OTE zone, Displacement, Institutional bias
  price_action_agent — Candle patterns, HH/HL structure, round numbers
  mtf_agent          — Multi-timeframe directional alignment
  ml_agent           — TabularPredictor + DeepPredictor + Stacking
  volume_agent       — Volume Profile + Order Flow imbalance
  sentiment_agent    — Traders Mood (contrarian) + Synthetic Sentiment
  rejection_zone_agent — Key price-level rejection memory

Regime Detector:
  detect_regime — trending / ranging / volatile / quiet
                  auto-adjusts per-agent weight boosts

Critic Agent:
  critic_agent — reads all agents, detects conflicts, adjusts confidence

Meta Controller:
  meta_controller — dynamic-weight final decision

Entry point:
  run_multi_agent(...)  — call once per 1M candle close
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from agent_brain import AgentResult, SharedBrain, shared_brain

log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────

def _dir(score: float, threshold: float = 0.15) -> str:
    if score > threshold:  return "GREEN"
    if score < -threshold: return "RED"
    return "NEUTRAL"


def _conf(score: float, base: float = 0.50, scale: float = 0.40, cap: float = 0.92) -> float:
    return min(cap, base + abs(score) * scale)


# ══════════════════════════════════════════════════════════════════
#  SPECIALIZED AGENTS
# ══════════════════════════════════════════════════════════════════

def smc_agent(pair: str, epoch: int, smc: dict) -> AgentResult:
    """SMC: Break of Structure, CHOCH, Order Block, FVG, Liquidity Sweep."""
    net   = float(smc.get("smc_net_score", 0))
    score = max(-1.0, min(1.0, net / 60.0))

    reasons: list[str] = []
    bos   = float(smc.get("bos", 0))
    choch = float(smc.get("choch", 0))
    ob    = float(smc.get("order_block_score", 0))
    fvg   = float(smc.get("fvg_score", 0))
    liq   = float(smc.get("liquidity_sweep", 0))
    hh_hl = int(smc.get("hh_hl_structure", 0))
    lh_ll = int(smc.get("lh_ll_structure", 0))
    in_ote = int(smc.get("in_ote_zone", 0))
    rn    = int(smc.get("round_number_near", 0))

    if bos > 0:    reasons.append(f"BOS bullish {bos:.0f}")
    elif bos < 0:  reasons.append(f"BOS bearish {bos:.0f}")
    if choch > 0:  reasons.append(f"CHOCH bullish {choch:.0f}")
    elif choch < 0: reasons.append(f"CHOCH bearish {choch:.0f}")
    if ob > 0:     reasons.append("Bullish Order Block active")
    elif ob < 0:   reasons.append("Bearish Order Block active")
    if abs(fvg) > 0: reasons.append(f"FVG {fvg:.0f}")
    if liq > 0:    reasons.append("Liquidity swept — reversal likely")
    if hh_hl:      reasons.append("HH/HL bullish structure")
    if lh_ll:      reasons.append("LH/LL bearish structure")
    if in_ote:     reasons.append("Price in OTE zone (61.8-78.6%)")
    if rn:         reasons.append("Round number confluence")

    return AgentResult(
        agent="smc", pair=pair, epoch=epoch,
        direction=_dir(score, 0.10),
        confidence=_conf(score, scale=0.38),
        score=score,
        reasons=reasons or ["SMC neutral"],
        data={"net": net, "bos": bos, "choch": choch, "ob": ob, "fvg": fvg,
              "liq": liq, "hh_hl": hh_hl, "lh_ll": lh_ll, "in_ote": in_ote},
    )


def ict_agent(pair: str, epoch: int, inst: dict, smc: dict, utc_hour: int) -> AgentResult:
    """ICT: Kill Zones, OTE, Displacement, Institutional bias score."""
    # Kill zone — check all three ICT session zones
    kz_london  = float(inst.get("kz_london_07_10", 0))
    kz_ny      = float(inst.get("kz_newyork_12_15", 0))
    kz_overlap = float(inst.get("kz_overlap_12_16", 0))
    kz_active  = kz_london > 0 or kz_ny > 0 or kz_overlap > 0
    kz_name    = ("London" if kz_london else ("NY" if kz_ny else ("Overlap" if kz_overlap else "off")))

    # OTE zone (61.8 – 78.6% Fibonacci retracement) from SMC
    ote_bull = int(smc.get("in_ote_zone_bull", 0))
    ote_bear = int(smc.get("in_ote_zone_bear", 0))

    # Displacement (large institutional candle)
    disp_bull = float(inst.get("displacement_bull", 0))
    disp_bear = float(inst.get("displacement_bear", 0))

    # Institutional composite score
    inst_bull = float(inst.get("inst_bull_score", 0))
    inst_bear = float(inst.get("inst_bear_score", 0))

    # Judas swing (liquidity grab before real move)
    judas_bull = float(inst.get("judas_swing_bull", 0))
    judas_bear = float(inst.get("judas_swing_bear", 0))

    # MM cycle
    mm_bull = float(inst.get("mm_bull_cycle", 0))
    mm_bear = float(inst.get("mm_bear_cycle", 0))

    # Net score: bull components - bear components, normalised
    bull_pts = inst_bull * 0.5 + disp_bull * 3 + ote_bull * 4 + judas_bull * 3 + mm_bull * 2
    bear_pts = inst_bear * 0.5 + disp_bear * 3 + ote_bear * 4 + judas_bear * 3 + mm_bear * 2
    raw = (bull_pts - bear_pts) / max(bull_pts + bear_pts + 1, 1)
    if kz_active:
        raw = raw * 1.25 if raw != 0 else 0.10  # Kill zone adds baseline confidence
    score = max(-1.0, min(1.0, raw))

    reasons: list[str] = []
    if kz_active:       reasons.append(f"Kill Zone active: {kz_name}")
    if ote_bull:        reasons.append("OTE zone — potential long")
    if ote_bear:        reasons.append("OTE zone — potential short")
    if disp_bull > 0:   reasons.append("Bullish displacement candle")
    if disp_bear > 0:   reasons.append("Bearish displacement candle")
    if judas_bull > 0:  reasons.append("Judas swing (bear trap)")
    if judas_bear > 0:  reasons.append("Judas swing (bull trap)")
    if mm_bull > 0:     reasons.append("MM bull cycle")
    if mm_bear > 0:     reasons.append("MM bear cycle")

    conf = _conf(score, scale=0.35)
    if kz_active:
        conf = min(conf * 1.08, 0.92)

    return AgentResult(
        agent="ict", pair=pair, epoch=epoch,
        direction=_dir(score, 0.15),
        confidence=conf,
        score=score,
        reasons=reasons or ["ICT neutral — no active zone"],
        data={"kz": kz_name, "ote_bull": ote_bull, "ote_bear": ote_bear,
              "disp_bull": disp_bull, "disp_bear": disp_bear,
              "inst_bull": inst_bull, "inst_bear": inst_bear},
    )


def price_action_agent(
    pair: str, epoch: int,
    reaction: dict,
    feat: dict,
    smc: dict,
) -> AgentResult:
    """Price Action: candle patterns, HH/HL structure, momentum."""
    net   = float(reaction.get("net_score", 0))
    score = max(-1.0, min(1.0, net / 5.0))

    reasons: list[str] = []

    # Candle patterns
    if reaction.get("bull_engulf"):
        score = min(score + 0.20, 1.0); reasons.append("Bullish Engulfing")
    if reaction.get("bear_engulf"):
        score = max(score - 0.20, -1.0); reasons.append("Bearish Engulfing")
    if reaction.get("pin_bull"):
        score = min(score + 0.15, 1.0); reasons.append("Pin Bar (bull)")
    if reaction.get("pin_bear"):
        score = max(score - 0.15, -1.0); reasons.append("Pin Bar (bear)")

    # Market structure from SMC (correct keys)
    hh_hl = int(smc.get("hh_hl_structure", 0))
    lh_ll = int(smc.get("lh_ll_structure", 0))
    if hh_hl:
        score = min(score + 0.10, 1.0); reasons.append("HH/HL bullish structure")
    if lh_ll:
        score = max(score - 0.10, -1.0); reasons.append("LH/LL bearish structure")

    # Round number proximity
    rn = int(smc.get("round_number_near", 0))
    if rn: reasons.append("Round number level")

    # Momentum from features (RSI, MACD)
    rsi = float(feat.get("rsi_14", 50))
    macd_hist = float(feat.get("macd_hist", 0))
    if rsi > 65 and macd_hist > 0:
        score = min(score + 0.08, 1.0); reasons.append(f"Bullish momentum RSI={rsi:.0f}")
    elif rsi < 35 and macd_hist < 0:
        score = max(score - 0.08, -1.0); reasons.append(f"Bearish momentum RSI={rsi:.0f}")

    # EMA stack
    c_vs_ema5  = float(feat.get("c_vs_ema5", 0))
    c_vs_ema20 = float(feat.get("c_vs_ema20", 0))
    if c_vs_ema5 > 0 and c_vs_ema20 > 0:
        reasons.append("Price above EMA5 & EMA20")
    elif c_vs_ema5 < 0 and c_vs_ema20 < 0:
        reasons.append("Price below EMA5 & EMA20")

    return AgentResult(
        agent="price_action", pair=pair, epoch=epoch,
        direction=_dir(score, 0.10),
        confidence=_conf(score, scale=0.35),
        score=score,
        reasons=reasons or ["PA neutral"],
        data={"net": net, "hh_hl": hh_hl, "lh_ll": lh_ll, "rsi": rsi, "rn": rn},
    )


def mtf_agent(pair: str, epoch: int, mtf: dict) -> AgentResult:
    """Multi-timeframe: directional alignment across 1m through 4h."""
    dir_str   = str(mtf.get("direction", "neutral"))
    agreement = float(mtf.get("agreement", 0.5))
    in_kz     = bool(mtf.get("in_killzone", False))

    if dir_str == "bull":
        score = agreement; agent_dir = "GREEN"
    elif dir_str == "bear":
        score = -agreement; agent_dir = "RED"
    else:
        score = 0.0; agent_dir = "NEUTRAL"

    tfs_dict = mtf.get("tfs", {})
    agreeing = [
        tf for tf, td in tfs_dict.items()
        if isinstance(td, dict) and (
            (dir_str == "bull" and td.get("bias") == "bull") or
            (dir_str == "bear" and td.get("bias") == "bear")
        )
    ]
    reasons = [f"MTF {dir_str} {agreement:.0%}"]
    if agreeing: reasons.append(f"TFs agree: {', '.join(agreeing)}")
    if in_kz:    reasons.append("In Kill Zone")

    return AgentResult(
        agent="mtf", pair=pair, epoch=epoch,
        direction=agent_dir,
        confidence=_conf(abs(score), scale=0.42),
        score=score,
        reasons=reasons,
        data={"direction": dir_str, "agreement": agreement,
              "in_kz": in_kz, "n_tfs": len(tfs_dict)},
    )


def ml_agent(
    pair: str, epoch: int,
    base_fused: float,
    all_model_probs: dict,
) -> AgentResult:
    """ML: Tabular + Deep + Stacking ensemble fused probability."""
    score     = (base_fused - 0.50) * 2.0   # -1.0 to +1.0
    agent_dir = "GREEN" if base_fused > 0.55 else ("RED" if base_fused < 0.45 else "NEUTRAL")
    conf      = min(0.92, abs(score) * 0.80 + 0.10)

    n     = len(all_model_probs)
    n_grn = sum(1 for p in all_model_probs.values() if p > 0.52)
    n_red = sum(1 for p in all_model_probs.values() if p < 0.48)

    reasons = [f"Fused prob {base_fused:.3f}"]
    if n > 0: reasons.append(f"Models: {n_grn}/{n} GREEN, {n_red}/{n} RED")

    # Top 3 most decisive models
    top = sorted(all_model_probs.items(), key=lambda x: abs(x[1] - 0.5), reverse=True)[:3]
    for name, prob in top:
        reasons.append(f"  {name}: {prob:.3f}")

    return AgentResult(
        agent="ml", pair=pair, epoch=epoch,
        direction=agent_dir,
        confidence=conf,
        score=score,
        reasons=reasons,
        data={"fused": base_fused, "n_models": n, "n_green": n_grn, "n_red": n_red},
    )


def volume_agent(
    pair: str, epoch: int,
    vp_info: dict, os_info: dict,
) -> AgentResult:
    """Volume Profile zone + Order Flow imbalance."""
    zone    = str(vp_info.get("vp_zone", "NEUTRAL"))
    at_poc  = bool(vp_info.get("vp_at_poc", False))
    imb     = float(os_info.get("os_imbalance", 0.0))

    score = 0.0; reasons: list[str] = []

    if zone == "AT_SUPPORT":
        score += 0.40; reasons.append("At VP Support")
    elif zone == "AT_RESISTANCE":
        score -= 0.40; reasons.append("At VP Resistance")
    elif zone == "BELOW_VALUE_AREA":
        score += 0.20; reasons.append("Below Value Area — bounce zone")
    elif zone == "ABOVE_VALUE_AREA":
        score -= 0.20; reasons.append("Above Value Area — reversal zone")
    elif zone == "LVN_FAST_MOVE":
        reasons.append("LVN — fast directional move possible")
    if at_poc:
        score *= 0.80; reasons.append("At POC — two-way caution")

    if imb > 0.30:
        score += 0.25; reasons.append(f"Buy imbalance {imb:.2f}")
    elif imb < -0.30:
        score -= 0.25; reasons.append(f"Sell imbalance {imb:.2f}")

    score = max(-1.0, min(1.0, score))
    return AgentResult(
        agent="volume", pair=pair, epoch=epoch,
        direction=_dir(score, 0.15),
        confidence=_conf(score, scale=0.35),
        score=score,
        reasons=reasons or ["Volume neutral"],
        data={"zone": zone, "at_poc": at_poc, "imbalance": imb},
    )


def sentiment_agent(
    pair: str, epoch: int,
    sent_info: dict, synth_info: dict,
) -> AgentResult:
    """Traders Mood (contrarian) + Synthetic Sentiment."""
    sent_mult  = float(sent_info.get("sent_mult", 1.0))
    sent_align = str(sent_info.get("sent_align", "neutral"))
    synth_bias = str(synth_info.get("synth_signal_bias", "NEUTRAL"))
    synth_net  = float(synth_info.get("synth_net_sentiment", 0.0))
    synth_exh  = float(synth_info.get("synth_exhaustion", 0.0))

    score = 0.0; reasons: list[str] = []

    if sent_mult > 1.05:
        score += 0.25; reasons.append(f"Sentiment aligns: {sent_align}")
    elif sent_mult < 0.95:
        score -= 0.25; reasons.append(f"Sentiment opposes: {sent_align}")

    if synth_bias == "GREEN":
        score += 0.30; reasons.append(f"Synthetic bullish {synth_net:.2f}")
    elif synth_bias == "RED":
        score -= 0.30; reasons.append(f"Synthetic bearish {synth_net:.2f}")

    if synth_exh > 0.70:
        reasons.append(f"Exhaustion signal {synth_exh:.2f}")

    score = max(-1.0, min(1.0, score))
    return AgentResult(
        agent="sentiment", pair=pair, epoch=epoch,
        direction=_dir(score, 0.15),
        confidence=_conf(score, scale=0.30, cap=0.85),
        score=score,
        reasons=reasons or ["Sentiment neutral"],
        data={"sent_mult": sent_mult, "synth_bias": synth_bias, "synth_net": synth_net},
    )


def rejection_zone_agent(pair: str, epoch: int, rz_info: dict) -> AgentResult:
    """Key price levels where price has repeatedly rejected."""
    rz_score   = float(rz_info.get("rz_score", 0))
    rz_sig     = str(rz_info.get("rz_signal", "NONE"))
    rz_grade   = str(rz_info.get("rz_grade", "D"))
    rz_touches = int(rz_info.get("rz_touches", 0))

    if rz_sig == "GREEN":
        score = rz_score / 100.0
        reasons = [f"RZ supports BUY — grade {rz_grade}, {rz_touches} touches"]
    elif rz_sig == "RED":
        score = -rz_score / 100.0
        reasons = [f"RZ supports SELL — grade {rz_grade}, {rz_touches} touches"]
    else:
        score = 0.0
        reasons = ["No active rejection zone near price"]

    return AgentResult(
        agent="rejection_zone", pair=pair, epoch=epoch,
        direction=_dir(score, 0.15),
        confidence=_conf(score, scale=0.42),
        score=score,
        reasons=reasons,
        data={"rz_score": rz_score, "rz_grade": rz_grade,
              "rz_touches": rz_touches, "rz_sig": rz_sig},
    )


# ══════════════════════════════════════════════════════════════════
#  REGIME DETECTOR
# ══════════════════════════════════════════════════════════════════

def detect_regime(df_1m: pd.DataFrame, adx: float) -> dict:
    """
    Identify market regime and return per-agent weight boosts.

    trending  (ADX >= 30) : SMC, ICT, MTF boosted
    volatile  (ATR spike) : Volume, Sentiment boosted
    ranging   (ADX < 20)  : RZ, Volume boosted
    quiet     (else)      : all neutral
    """
    atr_ratio = 1.0
    if len(df_1m) >= 15:
        try:
            cur_rng  = float(df_1m["high"].iloc[-1] - df_1m["low"].iloc[-1])
            avg_rng  = float((df_1m["high"] - df_1m["low"]).rolling(14).mean().iloc[-1])
            atr_ratio = cur_rng / max(avg_rng, 1e-10)
        except Exception:
            pass

    if adx >= 30:
        regime = "trending"
        boosts = {"smc": 1.50, "ict": 1.50, "mtf": 1.60,
                  "ml": 1.10, "price_action": 1.20,
                  "volume": 0.85, "sentiment": 0.75, "rejection_zone": 0.80}
    elif atr_ratio > 2.5:
        regime = "volatile"
        boosts = {"smc": 0.80, "ict": 0.80, "mtf": 1.10,
                  "ml": 0.90, "price_action": 0.90,
                  "volume": 1.40, "sentiment": 1.40, "rejection_zone": 1.20}
    elif adx < 20:
        regime = "ranging"
        boosts = {"smc": 0.85, "ict": 0.85, "mtf": 0.75,
                  "ml": 1.10, "price_action": 1.10,
                  "volume": 1.50, "sentiment": 1.20, "rejection_zone": 1.60}
    else:
        regime = "quiet"
        boosts = {a: 1.0 for a in
                  ["smc","ict","mtf","ml","price_action","volume","sentiment","rejection_zone"]}

    return {"regime": regime, "boosts": boosts, "adx": adx, "atr_ratio": round(atr_ratio, 2)}


# ══════════════════════════════════════════════════════════════════
#  CRITIC AGENT
# ══════════════════════════════════════════════════════════════════

def critic_agent(
    pair: str,
    epoch: int,
    brain: SharedBrain,
    proposed_dir: str,
    proposed_conf: float,
    regime: dict,
) -> dict:
    """
    Critic reads all agent outputs for this epoch.

    Agreement rules:
      >= 75% weighted agreement  -> boost  (+up to 10%)
      60-75% agreement           -> mild boost (+5%)
      40-60% agreement           -> penalty (-10%)
      < 40%  agreement           -> hard penalty (-25%)

    Extra penalties:
      MTF opposes                -> -10%
      SMC + ICT both oppose      -> -15%
    """
    epoch_results = {
        a: r for a, r in brain.read_all(pair).items()
        if r.epoch == epoch
    }
    if not epoch_results:
        return {"adj_conf": proposed_conf, "critique": [], "agreement": 0.5}

    boosts = regime.get("boosts", {})
    agree_wt = oppose_wt = neutral_wt = 0.0
    critique: list[str] = []

    for agent_name, result in epoch_results.items():
        base_w = brain.get_weight(pair, agent_name)
        eff_w  = base_w * boosts.get(agent_name, 1.0) * result.confidence

        if result.direction == proposed_dir:
            agree_wt += eff_w
        elif result.direction == "NEUTRAL":
            neutral_wt += eff_w * 0.5
        else:
            oppose_wt += eff_w
            critique.append(
                f"{agent_name}={result.direction} (w={eff_w:.2f})"
            )

    total = agree_wt + oppose_wt + neutral_wt or 1.0
    agreement = (agree_wt + neutral_wt * 0.5) / total

    if agreement >= 0.75:
        adj = min(proposed_conf * (1.0 + (agreement - 0.75) * 0.40), 0.99)
        critique.insert(0, f"Strong consensus {agreement:.0%}")
    elif agreement >= 0.60:
        adj = min(proposed_conf * 1.05, 0.99)
        critique.insert(0, f"Moderate consensus {agreement:.0%}")
    elif agreement >= 0.40:
        adj = proposed_conf * 0.90
        critique.insert(0, f"Mixed signals {agreement:.0%} -- penalised")
    else:
        adj = proposed_conf * 0.75
        critique.insert(0, f"Opposing consensus {agreement:.0%} -- hard penalty")

    # MTF opposition: extra -10%
    mtf_r = epoch_results.get("mtf")
    if mtf_r and mtf_r.direction not in (proposed_dir, "NEUTRAL"):
        adj *= 0.90
        critique.append("MTF opposes -- extra -10%")

    # SMC + ICT both oppose: hard -15%
    smc_r = epoch_results.get("smc")
    ict_r = epoch_results.get("ict")
    if (smc_r and ict_r and
            smc_r.direction not in (proposed_dir, "NEUTRAL") and
            ict_r.direction not in (proposed_dir, "NEUTRAL")):
        adj *= 0.85
        critique.append("SMC + ICT both oppose -- hard -15%")

    return {
        "adj_conf":  min(adj, 0.99),
        "critique":  critique[:6],
        "agreement": round(agreement, 3),
        "agree_wt":  round(agree_wt, 3),
        "oppose_wt": round(oppose_wt, 3),
    }


# ══════════════════════════════════════════════════════════════════
#  META CONTROLLER
# ══════════════════════════════════════════════════════════════════

def meta_controller(
    pair: str,
    epoch: int,
    brain: SharedBrain,
    pipeline_dir: str,
    pipeline_conf: float,
    regime: dict,
) -> dict:
    """
    Combines all agent votes (dynamic weights x regime boosts) into
    final direction + confidence.

    Overrides pipeline direction only when weighted vote strongly disagrees (>=55%).
    """
    # 1. Critic adjusts confidence
    crit     = critic_agent(pair, epoch, brain, pipeline_dir, pipeline_conf, regime)
    adj_conf = crit["adj_conf"]

    # 2. Weighted directional vote
    boosts   = regime.get("boosts", {})
    green_wt = red_wt = 0.0
    for agent_name, result in brain.read_all(pair).items():
        if result.epoch != epoch:
            continue
        base_w = brain.get_weight(pair, agent_name)
        eff_w  = base_w * boosts.get(agent_name, 1.0) * result.confidence
        if result.direction == "GREEN":
            green_wt += eff_w
        elif result.direction == "RED":
            red_wt   += eff_w

    # 3. Override direction only if vote is decisive (>=55%)
    total_vote = green_wt + red_wt or 1.0
    if green_wt / total_vote >= 0.55:
        final_dir = "GREEN"
    elif red_wt / total_vote >= 0.55:
        final_dir = "RED"
    else:
        final_dir = pipeline_dir   # indecisive — respect pipeline

    dir_changed = final_dir != pipeline_dir
    if dir_changed:
        adj_conf *= 0.93   # slight penalty for override

    # 4. Conflict penalty
    conflict = brain.conflict_score(pair, epoch)
    if conflict > 0.35:
        adj_conf *= (1.0 - conflict * 0.25)

    return {
        "final_direction":  final_dir,
        "final_confidence": min(adj_conf, 0.99),
        "orig_direction":   pipeline_dir,
        "dir_changed":      dir_changed,
        "regime":           regime.get("regime", "unknown"),
        "atr_ratio":        regime.get("atr_ratio", 1.0),
        "conflict_score":   round(conflict, 3),
        "agreement":        crit.get("agreement", 0.5),
        "critique":         crit.get("critique", []),
        "green_weight":     round(green_wt, 3),
        "red_weight":       round(red_wt, 3),
        "agent_weights":    brain.get_all_weights(pair),
    }


# ══════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT — called once per 1M candle close
# ══════════════════════════════════════════════════════════════════

def run_multi_agent(
    *,
    pair:            str,
    epoch:           int,
    df_1m:           pd.DataFrame,
    feat:            dict,
    adx:             float,
    smc:             dict,
    inst:            dict,
    reaction:        dict,
    mtf:             dict,
    base_fused:      float,
    all_model_probs: dict,
    vp_info:         dict,
    os_info:         dict,
    sent_info:       dict,
    synth_info:      dict,
    rz_info:         dict,
    pipeline_dir:    str,
    pipeline_conf:   float,
    utc_hour:        int,
) -> dict[str, Any]:
    """
    1. Run all 8 specialized agents, write results to SharedBrain.
    2. Detect market regime (trending / ranging / volatile / quiet).
    3. Critic reads all agents, detects conflicts, adjusts confidence.
    4. Meta Controller produces final direction + confidence.

    Returns dict with final_direction, final_confidence, and full agent report.
    """
    brain = shared_brain

    # ── Step 1: Run all agents ────────────────────────────────────
    agents_run = [
        smc_agent(pair, epoch, smc),
        ict_agent(pair, epoch, inst, smc, utc_hour),
        price_action_agent(pair, epoch, reaction, feat, smc),
        mtf_agent(pair, epoch, mtf),
        ml_agent(pair, epoch, base_fused, all_model_probs),
        volume_agent(pair, epoch, vp_info, os_info),
        sentiment_agent(pair, epoch, sent_info, synth_info),
        rejection_zone_agent(pair, epoch, rz_info),
    ]

    # Write all to SharedBrain — agents can read each other
    for r in agents_run:
        brain.write(r)

    log.debug(
        "Agents [%s @%d]: %s",
        pair, epoch,
        " | ".join(f"{r.agent}:{r.direction}({r.confidence:.2f})" for r in agents_run),
    )

    # ── Step 2: Regime ────────────────────────────────────────────
    regime = detect_regime(df_1m, adx)

    # ── Step 3: Meta Controller ───────────────────────────────────
    meta = meta_controller(pair, epoch, brain, pipeline_dir, pipeline_conf, regime)

    log.info(
        "MultiAgent %s: regime=%s conflict=%.2f agree=%.0f%% %s->%s conf %.3f->%.3f%s",
        pair,
        meta["regime"],
        meta["conflict_score"],
        meta["agreement"] * 100,
        meta["orig_direction"],
        meta["final_direction"],
        pipeline_conf,
        meta["final_confidence"],
        " (DIR CHANGED)" if meta["dir_changed"] else "",
    )

    return {
        "final_direction":  meta["final_direction"],
        "final_confidence": meta["final_confidence"],
        "regime":           meta["regime"],
        "atr_ratio":        meta["atr_ratio"],
        "conflict_score":   meta["conflict_score"],
        "agreement":        meta["agreement"],
        "dir_changed":      meta["dir_changed"],
        "critique":         meta["critique"],
        "green_weight":     meta["green_weight"],
        "red_weight":       meta["red_weight"],
        "agent_report":     brain.summary_for_pair(pair),
    }
