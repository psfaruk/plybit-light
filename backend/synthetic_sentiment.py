"""
Synthetic Sentiment Engine — derives buyer/seller pressure from price action
when broker-sent sentiment data is unavailable.

CORE IDEAS
─────────
1. Up moves on LOW range = weak bulls   → bearish bias
2. Down moves on HIGH range = real sellers → bearish confirmation
3. Repeated upper wicks      = selling pressure (rejection)
4. Repeated lower wicks      = buying  pressure
5. Price RISING but each candle SHRINKING = exhaustion (top)
6. Price FALLING but each candle SHRINKING = capitulation (bottom)
7. RSI divergence vs price   = hidden reversal

OUTPUT
─────
{
  buyer_ratio:    0..1     # implied buyers (synthetic)
  net_sentiment:  -1..+1   # negative=bearish, positive=bullish
  exhaustion:     0..1     # higher = closer to reversal
  divergence:     "BULL" | "BEAR" | "NONE"
  signal_bias:    "GREEN" | "RED" | "NEUTRAL"
}
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Any


def _safe_div(a: float, b: float) -> float:
    return a / b if abs(b) > 1e-10 else 0.0


def compute_synthetic_sentiment(df: pd.DataFrame, lookback: int = 30) -> dict[str, Any]:
    """
    Compute synthetic sentiment from OHLC.
    `df` must have columns: open, high, low, close (+ optional volume).
    Returns a flat dict ready to merge into signal output.
    """
    if len(df) < 10:
        return _empty()

    d = df.tail(lookback).copy()
    o = d["open"].astype(float).to_numpy()
    h = d["high"].astype(float).to_numpy()
    l = d["low"].astype(float).to_numpy()
    c = d["close"].astype(float).to_numpy()

    rng        = np.maximum(h - l, 1e-10)
    body       = np.abs(c - o)
    upper_wick = h - np.maximum(o, c)
    lower_wick = np.minimum(o, c) - l

    # ── 1. Wick-based pressure (last 10 candles, weighted) ────────
    n = min(10, len(d))
    w = np.linspace(0.5, 1.0, n)         # newer candles weighted higher
    seller_wick_p = float(np.average(upper_wick[-n:] / rng[-n:], weights=w))
    buyer_wick_p  = float(np.average(lower_wick[-n:] / rng[-n:], weights=w))

    # ── 2. Effort-vs-result (volume proxy = range) ────────────────
    # Up candles with shrinking range = weak bulls
    is_up      = c > o
    up_ranges  = rng[is_up]
    dn_ranges  = rng[~is_up]
    # Only compute effort imbalance when BOTH sides present (else 0)
    if len(up_ranges) > 0 and len(dn_ranges) > 0:
        avg_up_r = float(np.mean(up_ranges))
        avg_dn_r = float(np.mean(dn_ranges))
        effort_imbalance = _safe_div(avg_dn_r - avg_up_r, avg_dn_r + avg_up_r)
    else:
        effort_imbalance = 0.0
    # positive → sellers showing more conviction; negative → buyers

    # ── 3. Net body direction (signed) ────────────────────────────
    net_body = float(np.sum(np.where(is_up, body, -body)))
    total_body = float(np.sum(body)) + 1e-10
    body_dir = net_body / total_body          # -1..+1

    # ── 4. Exhaustion — shrinking momentum ────────────────────────
    if len(d) >= 6:
        first_3 = float(np.mean(body[-6:-3]))
        last_3  = float(np.mean(body[-3:]))
        shrink_ratio = _safe_div(first_3 - last_3, first_3)
        # positive shrink_ratio → body shrinking → exhaustion
        exhaustion = max(0.0, min(shrink_ratio, 1.0))
    else:
        exhaustion = 0.0

    # ── 5. RSI divergence (price vs RSI direction) ────────────────
    divergence = _rsi_divergence(c)

    # ── 6. Aggregate net sentiment ───────────────────────────────
    net_sentiment = (
        -seller_wick_p * 0.30
        + buyer_wick_p  * 0.30
        -effort_imbalance * 0.20
        + body_dir       * 0.20
    )
    net_sentiment = float(max(-1.0, min(net_sentiment, 1.0)))

    # Implied buyer ratio (mirror of net_sentiment in 0..1 space)
    buyer_ratio = 0.5 + net_sentiment / 2.0

    # ── 7. Signal bias ────────────────────────────────────────────
    if net_sentiment >  0.15 and not (exhaustion > 0.4):
        signal_bias = "GREEN"
    elif net_sentiment < -0.15 and not (exhaustion > 0.4):
        signal_bias = "RED"
    elif exhaustion > 0.5 and net_sentiment > 0.10:
        # bullish but exhausting → expect reversal down
        signal_bias = "RED"
    elif exhaustion > 0.5 and net_sentiment < -0.10:
        signal_bias = "GREEN"
    elif divergence == "BULL":
        signal_bias = "GREEN"
    elif divergence == "BEAR":
        signal_bias = "RED"
    else:
        signal_bias = "NEUTRAL"

    return {
        "synth_buyer_ratio":   round(buyer_ratio, 3),
        "synth_net_sentiment": round(net_sentiment, 3),
        "synth_exhaustion":    round(exhaustion, 3),
        "synth_divergence":    divergence,
        "synth_seller_wick":   round(seller_wick_p, 3),
        "synth_buyer_wick":    round(buyer_wick_p, 3),
        "synth_effort_imb":    round(effort_imbalance, 3),
        "synth_signal_bias":   signal_bias,
    }


def _rsi_divergence(closes: np.ndarray, period: int = 14, lookback: int = 14) -> str:
    """Detect bull/bear RSI divergence in recent window."""
    if len(closes) < period + lookback + 1:
        return "NONE"
    rsi = _rsi(closes, period)
    if rsi is None or len(rsi) < lookback:
        return "NONE"

    seg_prices = closes[-lookback:]
    seg_rsi    = rsi[-lookback:]

    p_lo_idx = int(np.argmin(seg_prices))
    p_hi_idx = int(np.argmax(seg_prices))
    r_lo_idx = int(np.argmin(seg_rsi))
    r_hi_idx = int(np.argmax(seg_rsi))

    # Bull divergence: price makes lower low, RSI makes higher low
    if p_lo_idx > 1 and len(seg_prices) > 2:
        first_half_p_lo = float(np.min(seg_prices[: p_lo_idx]))
        first_half_r_lo = float(np.min(seg_rsi[: p_lo_idx]))
        last_p_lo       = float(seg_prices[p_lo_idx])
        last_r_lo       = float(seg_rsi[p_lo_idx])
        if last_p_lo < first_half_p_lo * 0.998 and last_r_lo > first_half_r_lo:
            return "BULL"

    # Bear divergence: price higher high, RSI lower high
    if p_hi_idx > 1 and len(seg_prices) > 2:
        first_half_p_hi = float(np.max(seg_prices[: p_hi_idx]))
        first_half_r_hi = float(np.max(seg_rsi[: p_hi_idx]))
        last_p_hi       = float(seg_prices[p_hi_idx])
        last_r_hi       = float(seg_rsi[p_hi_idx])
        if last_p_hi > first_half_p_hi * 1.002 and last_r_hi < first_half_r_hi:
            return "BEAR"

    return "NONE"


def _rsi(prices: np.ndarray, period: int = 14) -> np.ndarray | None:
    if len(prices) < period + 1:
        return None
    deltas  = np.diff(prices)
    gains   = np.maximum(deltas, 0)
    losses  = -np.minimum(deltas, 0)
    avg_g   = np.convolve(gains,  np.ones(period)/period, mode="valid")
    avg_l   = np.convolve(losses, np.ones(period)/period, mode="valid")
    rs      = np.divide(avg_g, avg_l + 1e-10, out=np.zeros_like(avg_g), where=avg_l > 0)
    rsi     = 100 - 100 / (1 + rs)
    return rsi


def score_for_direction(synth: dict, direction: str) -> dict[str, Any]:
    """
    Return a confidence multiplier (0.80..1.18) for the proposed direction
    based on synthetic sentiment.
    """
    if not synth or synth.get("synth_signal_bias") == "NEUTRAL":
        return {"synth_mult": 1.0, "synth_align": "NEUTRAL"}

    bias       = synth.get("synth_signal_bias", "NEUTRAL")
    net        = float(synth.get("synth_net_sentiment", 0))
    exhaustion = float(synth.get("synth_exhaustion", 0))

    if bias == direction:
        # Aligned with direction — boost by strength
        mult = 1.0 + min(abs(net), 0.5) * 0.30        # max +15%
        if synth.get("synth_divergence") in ("BULL", "BEAR"):
            mult += 0.03                                # divergence bonus
        align = "ALIGNED"
    elif bias in ("GREEN", "RED"):
        # Opposing — penalty proportional to strength
        mult = 1.0 - min(abs(net), 0.5) * 0.30        # max -15%
        align = "OPPOSED"
    else:
        mult = 1.0
        align = "NEUTRAL"

    # Exhaustion is a contrarian flag — if direction matches recent strong
    # trend AND exhaustion is high, that's late entry → penalty
    if exhaustion > 0.5 and bias == direction:
        mult *= 0.92
        align = "ALIGNED_BUT_EXHAUSTED"

    return {
        "synth_mult":  round(mult, 3),
        "synth_align": align,
    }


def _empty() -> dict[str, Any]:
    return {
        "synth_buyer_ratio":   0.5,
        "synth_net_sentiment": 0.0,
        "synth_exhaustion":    0.0,
        "synth_divergence":    "NONE",
        "synth_seller_wick":   0.0,
        "synth_buyer_wick":    0.0,
        "synth_effort_imb":    0.0,
        "synth_signal_bias":   "NEUTRAL",
    }
