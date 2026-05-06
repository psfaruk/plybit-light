"""SMC/ICT (Smart Money Concepts) feature extraction — 40+ features."""

import numpy as np
import pandas as pd
from datetime import datetime, timezone


# ── Pure Price Action helpers (guide: HH/HL, round numbers, role reversal) ───

def _market_structure(swing_highs: list, swing_lows: list) -> tuple[bool, bool]:
    """HH+HL = confirmed uptrend; LH+LL = confirmed downtrend."""
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return False, False
    hh_hl = swing_highs[-1][1] > swing_highs[-2][1] and swing_lows[-1][1] > swing_lows[-2][1]
    lh_ll = swing_highs[-1][1] < swing_highs[-2][1] and swing_lows[-1][1] < swing_lows[-2][1]
    return bool(hh_hl), bool(lh_ll)


def _round_number_near(price: float, atr: float) -> tuple[bool, float]:
    """True + level if price is within 0.3×ATR of a significant round number."""
    tol = atr * 0.3
    # magnitude: e.g. price=4523 → mag=10; price=1.3245 → mag=0.01
    mag = 10 ** max(int(np.floor(np.log10(abs(price)))) - 1, -3)
    for mult in (1, 2, 5, 10, 50):
        interval = mag * mult
        nearest  = round(price / interval) * interval
        if abs(price - nearest) < tol:
            return True, float(nearest)
    return False, 0.0


def _role_reversal(
    swing_highs: list, swing_lows: list, price: float, atr: float
) -> tuple[bool, bool]:
    """
    Bull role reversal: price retesting a prior swing-high (broken resistance = new support).
    Bear role reversal: price retesting a prior swing-low (broken support = new resistance).
    """
    tol = atr * 0.6
    bull_rr = any(abs(price - sh) < tol for _, sh in swing_highs[:-1]) if len(swing_highs) >= 2 else False
    bear_rr = any(abs(price - sl) < tol for _, sl in swing_lows[:-1])  if len(swing_lows)  >= 2 else False
    return bool(bull_rr), bool(bear_rr)


def _momentum_candles(df: pd.DataFrame, n: int = 5) -> tuple[int, int]:
    """Count consecutive bull/bear candles ending at the most recent bar."""
    subset = df.tail(n)
    o_vals = subset["open"].values
    c_vals = subset["close"].values
    bull = bear = 0
    for o, c in zip(reversed(o_vals), reversed(c_vals)):
        if c > o:
            if bear: break
            bull += 1
        elif c < o:
            if bull: break
            bear += 1
        else:
            break
    return bull, bear


def _consolidation(df: pd.DataFrame, lookback: int = 5) -> bool:
    """True if recent candles are narrow (< 45% of 20-bar ATR) — ranging market."""
    recent_rng = (df["high"] - df["low"]).tail(lookback).mean()
    base_atr   = (df["high"] - df["low"]).tail(20).mean()
    return bool(recent_rng < base_atr * 0.45)


# ─────────────────────────────────────────────────────────────────────────────

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

    # Bug 5.1 fix: use prev closed candle price (not live tick) for BOS
    prev_closed = float(c.iloc[-2]) if len(c) >= 2 else float(c.iloc[-1])
    if len(swing_highs) >= 2 and prev_closed > swing_highs[-1][1]:
        features["bullish_bos"] = 1
        features["bearish_bos"] = 0
    elif len(swing_lows) >= 2 and prev_closed < swing_lows[-1][1]:
        features["bearish_bos"] = 1
        features["bullish_bos"] = 0
    else:
        features["bullish_bos"] = 0
        features["bearish_bos"] = 0

    # Bug 5.2 fix: swing-based ChoCh detection
    features["bullish_choch"] = 0
    features["bearish_choch"] = 0
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        last_sh, prior_sh = swing_highs[-1][1], swing_highs[-2][1]
        last_sl, prior_sl = swing_lows[-1][1],  swing_lows[-2][1]
        # Bullish ChoCh: broke above lower-high (downtrend reversal)
        if prev_closed > last_sh and last_sh < prior_sh:
            features["bullish_choch"] = 1
        # Bearish ChoCh: broke below higher-low (uptrend reversal)
        elif prev_closed < last_sl and last_sl > prior_sl:
            features["bearish_choch"] = 1

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
    atr_val = float((h - l).rolling(14).mean().iloc[-1]) + 1e-10
    tolerance = atr_val * 0.3  # ATR-relative (avoids % issues on gold vs forex)

    recent_highs = h.tail(20)
    recent_lows  = l.tail(20)
    bsl_level = float(recent_highs.max())
    ssl_level = float(recent_lows.min())

    equal_highs = (abs(recent_highs - bsl_level) < tolerance).sum() >= 2
    equal_lows  = (abs(recent_lows  - ssl_level) < tolerance).sum() >= 2
    features["buy_side_liquidity"]  = int(equal_highs)
    features["sell_side_liquidity"] = int(equal_lows)
    features["bsl_level"] = bsl_level if equal_highs else 0.0
    features["ssl_level"] = ssl_level if equal_lows  else 0.0

    # Sweep detection: wick-only beyond level, body stays inside → fake sweep
    prev_h2, prev_l2 = float(h.iloc[-2]), float(l.iloc[-2])
    prev_c2 = float(c.iloc[-2])
    bsl_swept = equal_highs and prev_h2 > bsl_level and prev_c2 < bsl_level
    ssl_swept = equal_lows  and prev_l2 < ssl_level and prev_c2 > ssl_level
    features["bsl_swept"] = int(bsl_swept)
    features["ssl_swept"] = int(ssl_swept)
    # Directional sweep signals: sweep of SSL → expect UP; BSL → expect DOWN
    features["liq_sweep_bull"] = int(ssl_swept)
    features["liq_sweep_bear"] = int(bsl_swept)

    # Legacy: any liquidity touched
    swept_high_leg = float(c.iloc[-1]) < float(h.tail(20).iloc[:-1].max()) and float(h.iloc[-1]) > float(h.tail(20).iloc[:-1].max())
    swept_low_leg  = float(c.iloc[-1]) > float(l.tail(20).iloc[:-1].min()) and float(l.iloc[-1]) < float(l.tail(20).iloc[:-1].min())
    features["liquidity_swept"] = int(swept_high_leg or swept_low_leg or bsl_swept or ssl_swept)

    # ── Pure Price Action — new guide features ───────────────
    hh_hl, lh_ll = _market_structure(swing_highs, swing_lows)
    features["hh_hl_structure"] = int(hh_hl)
    features["lh_ll_structure"] = int(lh_ll)

    rn_near, rn_level = _round_number_near(price, atr_val)
    features["round_number_near"]  = int(rn_near)
    features["round_number_level"] = rn_level

    rr_bull, rr_bear = _role_reversal(swing_highs, swing_lows, price, atr_val)
    features["role_reversal_bull"] = int(rr_bull)
    features["role_reversal_bear"] = int(rr_bear)

    mc_bull, mc_bear = _momentum_candles(df)
    features["momentum_bull_count"] = mc_bull
    features["momentum_bear_count"] = mc_bear

    features["consolidation"] = int(_consolidation(df))

    # Real breakout: big-body candle closing beyond previous 20-bar range
    body_now  = abs(float(c.iloc[-1]) - float(o.iloc[-1]))
    rng20_hi  = float(h.tail(21).iloc[:-1].max())
    rng20_lo  = float(l.tail(21).iloc[:-1].min())
    big_body  = body_now > atr_val * 0.6
    features["breakout_real_bull"] = int(big_body and float(c.iloc[-1]) > rng20_hi)
    features["breakout_real_bear"] = int(big_body and float(c.iloc[-1]) < rng20_lo)

    # ── Premium / Discount ───────────────────────────────────
    rng_high = h.tail(50).max()
    rng_low  = l.tail(50).min()
    rng_span = rng_high - rng_low + 1e-10
    price_pos = (price - rng_low) / rng_span

    features["in_premium_zone"]  = int(price_pos > 0.618)
    features["at_equilibrium"]   = int(0.45 < price_pos < 0.55)
    features["in_discount_zone"] = int(price_pos < 0.382)

    # Bug 5.3 fix: OTE uses actual swing retracement, not 50-candle range
    sw_high = swing_highs[-1][1] if swing_highs else float(h.tail(20).max())
    sw_low  = swing_lows[-1][1]  if swing_lows  else float(l.tail(20).min())
    sw_range = sw_high - sw_low + 1e-10
    bull_retrace = (sw_high - price) / sw_range
    bear_retrace = (price - sw_low)  / sw_range
    features["in_ote_zone_bull"] = int(0.62 < bull_retrace < 0.79)
    features["in_ote_zone_bear"] = int(0.62 < bear_retrace < 0.79)
    features["in_ote_zone"]      = int(features["in_ote_zone_bull"] or features["in_ote_zone_bear"])

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
    # ICT liquidity rules:
    # BSL (equal highs above) = bulls will run price UP to sweep them → bull pressure
    # SSL (equal lows below)  = bears will run price DOWN to sweep them → bear pressure
    # liq_sweep_bull (SSL just swept) → reversal UP expected
    # liq_sweep_bear (BSL just swept) → reversal DOWN expected
    bull_score = (
        features["price_at_bullish_ob"] * 10 +
        features["price_in_bullish_fvg"] * 8 +
        features["bullish_bos"] * 7 +
        features["in_ote_zone_bull"] * 5 +
        features["in_discount_zone"] * 4 +
        features["buy_side_liquidity"] * 4 +      # BSL above → price will run UP
        features["liq_sweep_bull"] * 8 +           # SSL swept → reversal UP
        features["kz_london"] * 3 +
        features["kz_overlap"] * 3 +
        features["bullish_choch"] * 6 +
        features["hh_hl_structure"] * 5 +          # HH+HL confirmed uptrend
        features["role_reversal_bull"] * 6 +        # broken resistance = new support
        min(features["momentum_bull_count"], 3) * 2 + # consecutive bull candles
        features["breakout_real_bull"] * 4
    )
    bear_score = (
        features["price_at_bearish_ob"] * 10 +
        features["price_in_bearish_fvg"] * 8 +
        features["bearish_bos"] * 7 +
        features["in_ote_zone_bear"] * 5 +
        features["in_premium_zone"] * 4 +
        features["sell_side_liquidity"] * 4 +      # SSL below → price will run DOWN
        features["liq_sweep_bear"] * 8 +            # BSL swept → reversal DOWN
        features["kz_london"] * 3 +
        features["kz_overlap"] * 3 +
        features["bearish_choch"] * 6 +
        features["lh_ll_structure"] * 5 +           # LH+LL confirmed downtrend
        features["role_reversal_bear"] * 6 +         # broken support = new resistance
        min(features["momentum_bear_count"], 3) * 2 + # consecutive bear candles
        features["breakout_real_bear"] * 4
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
        "bsl_level": 0.0, "ssl_level": 0.0,
        "bsl_swept": 0, "ssl_swept": 0,
        "liq_sweep_bull": 0, "liq_sweep_bear": 0,
        "liquidity_swept": 0,
        "in_premium_zone": 0, "at_equilibrium": 1,
        "in_discount_zone": 0, "in_ote_zone": 0,
        "in_ote_zone_bull": 0, "in_ote_zone_bear": 0,
        "kz_asian": 0, "kz_london": 0, "kz_newyork": 0,
        "kz_overlap": 0, "kz_london_close": 0, "kz_gold": 0,
        "trend": "range",
        "hh_hl_structure": 0, "lh_ll_structure": 0,
        "round_number_near": 0, "round_number_level": 0.0,
        "role_reversal_bull": 0, "role_reversal_bear": 0,
        "momentum_bull_count": 0, "momentum_bear_count": 0,
        "consolidation": 0,
        "breakout_real_bull": 0, "breakout_real_bear": 0,
        "smc_bull_score": 0, "smc_bear_score": 0, "smc_net_score": 0,
    }
