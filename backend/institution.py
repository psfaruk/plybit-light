"""Institutional analysis: Liquidity sweeps, Judas swing, Market Maker cycle, PO3."""

import pandas as pd
import numpy as np


def compute_institutional(df: pd.DataFrame, utc_hour: int = 0, utc_minute: int = 0) -> dict[str, float]:
    if len(df) < 20:
        return _zero()

    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    vol = df.get("volume", pd.Series(np.ones(len(df)), index=df.index))

    price = float(c.iloc[-1])
    atr   = float((h - l).rolling(14).mean().iloc[-1])
    result: dict[str, float] = {}

    # ── Liquidity Sweeps ─────────────────────────────────────
    prev_high = float(h.tail(20).iloc[:-1].max())
    prev_low  = float(l.tail(20).iloc[:-1].min())
    swept_high = float(h.iloc[-1]) > prev_high and price < prev_high
    swept_low  = float(l.iloc[-1]) < prev_low  and price > prev_low
    result["bullish_liquidity_sweep"] = float(swept_low)
    result["bearish_liquidity_sweep"] = float(swept_high)

    # ── Institutional Candles ────────────────────────────────
    last_body = abs(float(c.iloc[-1]) - float(o.iloc[-1]))
    result["institutional_bullish"] = float(last_body >= atr * 1.5 and float(c.iloc[-1]) > float(o.iloc[-1]))
    result["institutional_bearish"] = float(last_body >= atr * 1.5 and float(c.iloc[-1]) < float(o.iloc[-1]))

    # ── Killzones ────────────────────────────────────────────
    result["kz_london_07_10"]  = float(7  <= utc_hour < 10)
    result["kz_newyork_12_15"] = float(12 <= utc_hour < 15)
    result["kz_overlap_12_16"] = float(12 <= utc_hour < 16)

    # ── Judas Swing ─────────────────────────────────────────
    # London swept prior low then closed bullish → bull Judas
    in_london = 7 <= utc_hour < 10
    result["judas_swing_bull"] = float(in_london and swept_low  and float(c.iloc[-1]) > float(o.iloc[-1]))
    result["judas_swing_bear"] = float(in_london and swept_high and float(c.iloc[-1]) < float(o.iloc[-1]))

    # ── Market Maker Cycle ───────────────────────────────────
    # Detect accumulation (range) → then strong directional move
    rng_5 = float((h.tail(5).max() - l.tail(5).min()))
    rng_20= float((h.tail(20).max() - l.tail(20).min()))
    in_range  = rng_5 < rng_20 * 0.25
    bull_move = float(c.iloc[-1]) > float(c.iloc[-5]) and float(c.iloc[-1]) - float(c.iloc[-5]) > atr * 1.5
    bear_move = float(c.iloc[-5]) > float(c.iloc[-1]) and float(c.iloc[-5]) - float(c.iloc[-1]) > atr * 1.5
    result["mm_bull_cycle"] = float(in_range and bull_move)
    result["mm_bear_cycle"] = float(in_range and bear_move)

    # ── Displacement ─────────────────────────────────────────
    last_body_pct = last_body / (atr + 1e-10)
    result["displacement_bull"] = float(last_body_pct > 1.8 and float(c.iloc[-1]) > float(o.iloc[-1]))
    result["displacement_bear"] = float(last_body_pct > 1.8 and float(c.iloc[-1]) < float(o.iloc[-1]))

    # ── Divergence ───────────────────────────────────────────
    # RSI divergence: price makes new low but RSI doesn't
    delta = c.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi   = 100 - 100 / (1 + gain / (loss + 1e-10))
    price_new_low = float(l.iloc[-1]) < float(l.tail(10).iloc[:-1].min())
    rsi_higher    = float(rsi.iloc[-1]) > float(rsi.tail(10).iloc[:-1].min())
    price_new_high= float(h.iloc[-1]) > float(h.tail(10).iloc[:-1].max())
    rsi_lower     = float(rsi.iloc[-1]) < float(rsi.tail(10).iloc[:-1].max())
    result["bullish_divergence"] = float(price_new_low  and rsi_higher)
    result["bearish_divergence"] = float(price_new_high and rsi_lower)

    # ── Asian Range ──────────────────────────────────────────
    # Mark the Asian range high/low from prior session (simple approximation)
    asian_mask = (df.index % 86400 < 21600) if hasattr(df.index, '__mod__') else pd.Series([False]*len(df))
    result["asian_range_high"] = float(h.tail(360).max())
    result["asian_range_low"]  = float(l.tail(360).min())

    # ── PO3 (Power of Three) ─────────────────────────────────
    result["po3_accumulation"] = float(0 <= utc_hour < 7)
    result["po3_manipulation"] = float(7 <= utc_hour < 12)
    result["po3_distribution"] = float(12 <= utc_hour < 20)

    # ── Composite Score ──────────────────────────────────────
    inst_bull = (
        result["bullish_liquidity_sweep"] * 8 +
        result["institutional_bullish"]   * 6 +
        result["judas_swing_bull"]         * 6 +
        result["mm_bull_cycle"]            * 5 +
        result["displacement_bull"]        * 5 +
        result["bullish_divergence"]       * 4 +
        result["kz_overlap_12_16"]         * 3
    )
    inst_bear = (
        result["bearish_liquidity_sweep"] * 8 +
        result["institutional_bearish"]   * 6 +
        result["judas_swing_bear"]         * 6 +
        result["mm_bear_cycle"]            * 5 +
        result["displacement_bear"]        * 5 +
        result["bearish_divergence"]       * 4 +
        result["kz_overlap_12_16"]         * 3
    )
    result["inst_bull_score"] = inst_bull
    result["inst_bear_score"] = inst_bear

    return result


def _zero() -> dict[str, float]:
    keys = [
        "bullish_liquidity_sweep", "bearish_liquidity_sweep",
        "institutional_bullish", "institutional_bearish",
        "kz_london_07_10", "kz_newyork_12_15", "kz_overlap_12_16",
        "judas_swing_bull", "judas_swing_bear",
        "mm_bull_cycle", "mm_bear_cycle",
        "displacement_bull", "displacement_bear",
        "bullish_divergence", "bearish_divergence",
        "asian_range_high", "asian_range_low",
        "po3_accumulation", "po3_manipulation", "po3_distribution",
        "inst_bull_score", "inst_bear_score",
    ]
    return {k: 0.0 for k in keys}
