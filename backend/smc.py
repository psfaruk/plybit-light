"""SMC/ICT (Smart Money Concepts) feature extraction — 31+ features."""

import numpy as np
import pandas as pd
from datetime import datetime, timezone


def _swing_points(high: pd.Series, low: pd.Series, n: int = 5):
    swing_highs = []
    swing_lows  = []
    for i in range(n, len(high) - n):
        if all(high.iloc[i] >= high.iloc[i-j] for j in range(1, n+1)) and \
           all(high.iloc[i] >= high.iloc[i+j] for j in range(1, n+1)):
            swing_highs.append((i, high.iloc[i]))
        if all(low.iloc[i] <= low.iloc[i-j] for j in range(1, n+1)) and \
           all(low.iloc[i] <= low.iloc[i+j] for j in range(1, n+1)):
            swing_lows.append((i, low.iloc[i]))
    return swing_highs, swing_lows


def _find_order_blocks(df: pd.DataFrame, lookback: int = 50):
    """Find bullish and bearish order blocks."""
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    bull_obs, bear_obs = [], []

    for i in range(lookback, len(df) - 3):
        # Bullish OB: bearish candle followed by strong bullish move
        if c.iloc[i] < o.iloc[i]:  # bearish candle
            if c.iloc[i+1] > h.iloc[i]:  # next candle breaks above
                bull_obs.append({"low": l.iloc[i], "high": h.iloc[i], "idx": i})
        # Bearish OB: bullish candle followed by strong bearish move
        if c.iloc[i] > o.iloc[i]:  # bullish candle
            if c.iloc[i+1] < l.iloc[i]:  # next candle breaks below
                bear_obs.append({"low": l.iloc[i], "high": h.iloc[i], "idx": i})

    return bull_obs[-3:] if bull_obs else [], bear_obs[-3:] if bear_obs else []


def _find_fvg(df: pd.DataFrame, lookback: int = 50):
    """Find Fair Value Gaps (imbalances)."""
    h, l = df["high"], df["low"]
    bull_fvgs, bear_fvgs = [], []

    for i in range(1, min(lookback, len(df) - 1)):
        idx = len(df) - 1 - i
        if idx < 2:
            continue
        # Bullish FVG: gap between candle[i-1].high and candle[i+1].low
        if l.iloc[idx+1] > h.iloc[idx-1]:
            bull_fvgs.append({
                "low": h.iloc[idx-1], "high": l.iloc[idx+1],
                "mid": (h.iloc[idx-1] + l.iloc[idx+1]) / 2,
                "idx": idx,
            })
        # Bearish FVG: gap between candle[i-1].low and candle[i+1].high
        if h.iloc[idx+1] < l.iloc[idx-1]:
            bear_fvgs.append({
                "low": h.iloc[idx+1], "high": l.iloc[idx-1],
                "mid": (l.iloc[idx-1] + h.iloc[idx+1]) / 2,
                "idx": idx,
            })

    return bull_fvgs[:3], bear_fvgs[:3]


def compute_smc_features(df: pd.DataFrame, utc_hour: int = 0) -> dict:
    if len(df) < 20:
        return _empty_smc()

    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    price = c.iloc[-1]
    features = {}

    # ── Market Structure ─────────────────────────────────────
    swing_highs, swing_lows = _swing_points(h, l, 5)
    features["swing_high_5"] = swing_highs[-1][1] if swing_highs else h.max()
    features["swing_low_5"]  = swing_lows[-1][1]  if swing_lows  else l.min()

    # BOS detection
    if len(swing_highs) >= 2 and price > swing_highs[-1][1]:
        features["bullish_bos"] = 1
        features["bearish_bos"] = 0
    elif len(swing_lows) >= 2 and price < swing_lows[-1][1]:
        features["bearish_bos"] = 1
        features["bullish_bos"] = 0
    else:
        features["bullish_bos"] = 0
        features["bearish_bos"] = 0

    # ChoCh: break in opposite direction after structure shift
    recent_dir = 1 if c.iloc[-1] > c.iloc[-5] else -1
    prev_dir   = 1 if c.iloc[-5] > c.iloc[-10] else -1
    features["bullish_choch"] = int(recent_dir ==  1 and prev_dir == -1)
    features["bearish_choch"] = int(recent_dir == -1 and prev_dir ==  1)

    # ── Order Blocks ─────────────────────────────────────────
    bull_obs, bear_obs = _find_order_blocks(df)
    at_bull_ob = any(ob["low"] <= price <= ob["high"] for ob in bull_obs)
    at_bear_ob = any(ob["low"] <= price <= ob["high"] for ob in bear_obs)
    features["price_at_bullish_ob"] = int(at_bull_ob)
    features["price_at_bearish_ob"] = int(at_bear_ob)
    features["bullish_ob_zone"]     = bull_obs[-1] if bull_obs else {}
    features["bearish_ob_zone"]     = bear_obs[-1] if bear_obs else {}

    # Breaker block: OB that was broken becomes breaker
    features["breaker_block"] = 0

    # ── Fair Value Gaps ──────────────────────────────────────
    bull_fvgs, bear_fvgs = _find_fvg(df)
    in_bull_fvg = any(fvg["low"] <= price <= fvg["high"] for fvg in bull_fvgs)
    in_bear_fvg = any(fvg["low"] <= price <= fvg["high"] for fvg in bear_fvgs)
    features["price_in_bullish_fvg"] = int(in_bull_fvg)
    features["price_in_bearish_fvg"] = int(in_bear_fvg)
    features["bullish_fvg"]  = int(bool(bull_fvgs))
    features["bearish_fvg"]  = int(bool(bear_fvgs))
    features["fvg_50_level"] = bull_fvgs[0]["mid"] if bull_fvgs else 0

    # ── Liquidity ────────────────────────────────────────────
    recent_highs = h.tail(20)
    recent_lows  = l.tail(20)
    high_range   = recent_highs.max() - recent_highs.min()
    low_range    = recent_lows.max()  - recent_lows.min()

    equal_highs  = (recent_highs > recent_highs.max() - high_range * 0.05).sum() >= 2
    equal_lows   = (recent_lows  < recent_lows.min()  + low_range  * 0.05).sum() >= 2
    features["buy_side_liquidity"]  = int(equal_highs)
    features["sell_side_liquidity"] = int(equal_lows)

    prev_high = h.iloc[-2]
    prev_low  = l.iloc[-2]
    swept_high = c.iloc[-1] < prev_high and h.iloc[-1] > prev_high
    swept_low  = c.iloc[-1] > prev_low  and l.iloc[-1] < prev_low
    features["liquidity_swept"] = int(swept_high or swept_low)

    # ── Premium / Discount ───────────────────────────────────
    rng_high = h.tail(50).max()
    rng_low  = l.tail(50).min()
    rng_span = rng_high - rng_low + 1e-10
    price_pos = (price - rng_low) / rng_span

    features["in_premium_zone"]  = int(price_pos > 0.618)
    features["at_equilibrium"]   = int(0.45 < price_pos < 0.55)
    features["in_discount_zone"] = int(price_pos < 0.382)
    features["in_ote_zone"]      = int(0.62 < price_pos < 0.79)

    # ── Sessions (Killzones) ─────────────────────────────────
    features["kz_asian"]       = int(0  <= utc_hour < 3)
    features["kz_london"]      = int(7  <= utc_hour < 10)
    features["kz_newyork"]     = int(12 <= utc_hour < 15)
    features["kz_overlap"]     = int(12 <= utc_hour < 16)
    features["kz_london_close"]= int(15 <= utc_hour < 16)
    features["kz_gold"]        = int(utc_hour == 13)

    # ── Trend ────────────────────────────────────────────────
    ema20 = c.ewm(span=20, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    if ema20.iloc[-1] > ema50.iloc[-1]:
        features["trend"] = "up"
    elif ema20.iloc[-1] < ema50.iloc[-1]:
        features["trend"] = "down"
    else:
        features["trend"] = "range"

    # ── Composite Scores ─────────────────────────────────────
    bull_score = (
        features["price_at_bullish_ob"] * 10 +
        features["price_in_bullish_fvg"] * 8 +
        features["bullish_bos"] * 7 +
        features["in_ote_zone"] * 5 +
        features["sell_side_liquidity"] * 5 +
        features["liquidity_swept"] * 4 +
        features["kz_london"] * 3 +
        features["kz_overlap"] * 3 +
        features["bullish_choch"] * 6
    )
    bear_score = (
        features["price_at_bearish_ob"] * 10 +
        features["price_in_bearish_fvg"] * 8 +
        features["bearish_bos"] * 7 +
        features["in_premium_zone"] * 5 +
        features["buy_side_liquidity"] * 5 +
        features["liquidity_swept"] * 4 +
        features["kz_london"] * 3 +
        features["kz_overlap"] * 3 +
        features["bearish_choch"] * 6
    )
    features["smc_bull_score"] = bull_score
    features["smc_bear_score"] = bear_score
    features["smc_net_score"]  = bull_score - bear_score

    return features


def _empty_smc() -> dict:
    return {
        "swing_high_5": 0, "swing_low_5": 0,
        "bullish_bos": 0, "bearish_bos": 0,
        "bullish_choch": 0, "bearish_choch": 0,
        "bullish_ob_zone": {}, "bearish_ob_zone": {},
        "price_at_bullish_ob": 0, "price_at_bearish_ob": 0,
        "breaker_block": 0,
        "bullish_fvg": 0, "bearish_fvg": 0,
        "price_in_bullish_fvg": 0, "price_in_bearish_fvg": 0,
        "fvg_50_level": 0,
        "buy_side_liquidity": 0, "sell_side_liquidity": 0,
        "liquidity_swept": 0,
        "in_premium_zone": 0, "at_equilibrium": 1,
        "in_discount_zone": 0, "in_ote_zone": 0,
        "kz_asian": 0, "kz_london": 0, "kz_newyork": 0,
        "kz_overlap": 0, "kz_london_close": 0, "kz_gold": 0,
        "trend": "range",
        "smc_bull_score": 0, "smc_bear_score": 0, "smc_net_score": 0,
    }
