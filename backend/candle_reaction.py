"""
Candle Reaction Engine — Python implementation.
Rust FFI version (PyO3) used in production if compiled.
Falls back to this pure-Python implementation.
"""

import numpy as np
import pandas as pd


def _body(o: float, c: float) -> float:
    return abs(c - o)


def _range(h: float, l: float) -> float:
    return max(h - l, 1e-10)


def compute_candle_reaction(
    df: pd.DataFrame,
    smc: dict | None = None,
    price_hold: dict[str, float] | None = None,
) -> dict[str, float]:
    """
    8-component candle reaction score (7 candle + 1 institutional hold).
    Returns bull_score, bear_score, net_score (all 0-100), and signal_boost.
    """
    if len(df) < 3:
        return _zero()

    last  = df.iloc[-1]
    prev  = df.iloc[-2]

    o, h, l, c = float(last["open"]), float(last["high"]), float(last["low"]), float(last["close"])
    po, pc = float(prev["open"]), float(prev["close"])

    rng  = _range(h, l)
    body = _body(o, c)

    # Bug 6.3 fix: True ATR (include prev close for gaps)
    trs: list[float] = []
    for i in range(max(1, len(df) - 14), len(df)):
        h_i  = float(df.iloc[i]["high"])
        l_i  = float(df.iloc[i]["low"])
        pc_i = float(df.iloc[i - 1]["close"])
        trs.append(max(h_i - l_i, abs(h_i - pc_i), abs(l_i - pc_i)))
    atr = float(np.mean(trs)) if trs else rng

    close_pos = (c - l) / rng  # 0=at low, 1=at high

    # ── 1. Wick Rejection ────────────────────────────────────
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    bull_wick_score = (lower_wick / rng) * 100
    bear_wick_score = (upper_wick / rng) * 100

    # ── 2. Pin Bar Quality ───────────────────────────────────
    pin_bull = int(lower_wick >= body * 2 and body <= rng * 0.25)
    pin_bear = int(upper_wick >= body * 2 and body <= rng * 0.25)
    pin_bull_score = 85.0 if pin_bull else (lower_wick / rng * 60)
    pin_bear_score = 85.0 if pin_bear else (upper_wick / rng * 60)

    # ── 3. Body Position ─────────────────────────────────────
    bull_body_score = close_pos * 100
    bear_body_score = (1 - close_pos) * 100

    # ── 4. Momentum Confirmation ─────────────────────────────
    bull_mom = int(c > pc and c > o)
    bear_mom = int(c < pc and c < o)
    bull_mom_score = 75.0 if bull_mom else 20.0
    bear_mom_score = 75.0 if bear_mom else 20.0

    # ── 5. ATR Relative Size ─────────────────────────────────
    atr_score = min((rng / (atr + 1e-10)) * 70, 100.0)

    # Bug 6.2 fix: standard body engulfing (body engulfs body, not wick)
    prev_body = _body(po, pc)
    prev_body_top    = max(po, pc)
    prev_body_bottom = min(po, pc)
    bull_engulf = int(c > o and c >= prev_body_top and o <= prev_body_bottom and body > prev_body)
    bear_engulf = int(c < o and c <= prev_body_bottom and o >= prev_body_top and body > prev_body)
    bull_engulf_score = 95.0 if bull_engulf else 0.0
    bear_engulf_score = 95.0 if bear_engulf else 0.0

    # Zone reaction — directional: only adds to the aligned side
    bull_zone_score = 0.0
    bear_zone_score = 0.0
    if smc:
        candle_mid = (h + l) / 2.0
        at_bull = smc.get("price_in_bullish_fvg") or smc.get("price_at_bullish_ob")
        at_bear = smc.get("price_in_bearish_fvg") or smc.get("price_at_bearish_ob")
        if at_bull and c > candle_mid:
            bull_zone_score = 90.0  # close above mid at bullish zone → bull confirmation
        elif at_bear and c < candle_mid:
            bear_zone_score = 90.0  # close below mid at bearish zone → bear confirmation

    # ── 8. Price Hold (Institutional Order Absorption) ──────────
    # hold_strength 0-100; velocity reveals break direction after hold
    hold_strength = float(price_hold.get("hold_strength", 0)) if price_hold else 0.0
    tick_velocity = float(price_hold.get("tick_velocity", 0)) if price_hold else 0.0
    # Bull hold: price held then broke upward (velocity > 0) or just held bullish candle
    # Bear hold: price held then broke downward (velocity < 0)
    bull_hold_score = hold_strength if (tick_velocity >= 0) else hold_strength * 0.2
    bear_hold_score = hold_strength if (tick_velocity <= 0) else hold_strength * 0.2

    # Weighted composite — hold weight 0.07 redistributed from zone (0.05→0.03)
    weights = [0.19, 0.17, 0.17, 0.14, 0.10, 0.13, 0.03, 0.07]
    bull_components = [
        bull_wick_score, pin_bull_score, bull_body_score,
        bull_mom_score, atr_score, bull_engulf_score, bull_zone_score, bull_hold_score,
    ]
    bear_components = [
        bear_wick_score, pin_bear_score, bear_body_score,
        bear_mom_score, atr_score, bear_engulf_score, bear_zone_score, bear_hold_score,
    ]

    bull_score = float(sum(w * s for w, s in zip(weights, bull_components)))
    bear_score = float(sum(w * s for w, s in zip(weights, bear_components)))
    net_score  = bull_score - bear_score

    return {
        "bull_score":         bull_score,
        "bear_score":         bear_score,
        "net_score":          net_score,
        "signal_boost":       (net_score / 100.0) * 0.12,
        "pin_bar":            float(pin_bull or pin_bear),
        "engulfing":          float(bull_engulf or bear_engulf),
        "pin_bull":           float(pin_bull),
        "pin_bear":           float(pin_bear),
        "bull_engulf":        float(bull_engulf),
        "bear_engulf":        float(bear_engulf),
        "atr_score":          atr_score,
        "close_pos":          close_pos,
        "close_upper_third":  float(close_pos > 0.67),
        "close_lower_third":  float(close_pos < 0.33),
        "hold_strength":      hold_strength,
        "hold_active":        float((price_hold or {}).get("price_hold_active", 0)),
    }


def _zero() -> dict[str, float]:
    return {
        "bull_score": 50.0, "bear_score": 50.0, "net_score": 0.0,
        "signal_boost": 0.0, "pin_bar": 0.0, "engulfing": 0.0,
        "pin_bull": 0.0, "pin_bear": 0.0,
        "bull_engulf": 0.0, "bear_engulf": 0.0,
        "atr_score": 50.0, "close_pos": 0.5,
        "close_upper_third": 0.0, "close_lower_third": 0.0,
        "hold_strength": 0.0, "hold_active": 0.0,
    }
