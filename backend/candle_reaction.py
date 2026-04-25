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


def compute_candle_reaction(df: pd.DataFrame) -> dict[str, float]:
    """
    7-component candle reaction score.
    Returns bull_score, bear_score, net_score (all 0-100), and signal_boost.
    """
    if len(df) < 3:
        return _zero()

    last  = df.iloc[-1]
    prev  = df.iloc[-2]
    prev2 = df.iloc[-3]

    o, h, l, c = float(last["open"]), float(last["high"]), float(last["low"]), float(last["close"])
    po, ph, pl, pc = float(prev["open"]), float(prev["high"]), float(prev["low"]), float(prev["close"])

    rng  = _range(h, l)
    body = _body(o, c)

    # ATR from last 14 candles
    trs  = [(float(df.iloc[i]["high"]) - float(df.iloc[i]["low"])) for i in range(max(0, len(df)-14), len(df))]
    atr  = float(np.mean(trs)) if trs else rng

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

    # ── 6. Engulfing Detection ───────────────────────────────
    prev_body = _body(po, pc)
    bull_engulf = int(c > o and c > ph and o < pl and body > prev_body)
    bear_engulf = int(c < o and c < pl and o > ph and body > prev_body)
    bull_engulf_score = 95.0 if bull_engulf else 0.0
    bear_engulf_score = 95.0 if bear_engulf else 0.0

    # ── 7. Zone Reaction (placeholder — needs OB/FVG zones) ──
    zone_score = 0.0

    # Weighted composite
    weights = [0.20, 0.18, 0.18, 0.15, 0.10, 0.14, 0.05]
    bull_components = [
        bull_wick_score, pin_bull_score, bull_body_score,
        bull_mom_score, atr_score, bull_engulf_score, zone_score,
    ]
    bear_components = [
        bear_wick_score, pin_bear_score, bear_body_score,
        bear_mom_score, atr_score, bear_engulf_score, zone_score,
    ]

    bull_score = float(sum(w * s for w, s in zip(weights, bull_components)))
    bear_score = float(sum(w * s for w, s in zip(weights, bear_components)))
    net_score  = bull_score - bear_score

    return {
        "bull_score":    bull_score,
        "bear_score":    bear_score,
        "net_score":     net_score,
        "signal_boost":  (net_score / 100.0) * 0.12,
        "pin_bar":       float(pin_bull or pin_bear),
        "engulfing":     float(bull_engulf or bear_engulf),
        "pin_bull":      float(pin_bull),
        "pin_bear":      float(pin_bear),
        "bull_engulf":   float(bull_engulf),
        "bear_engulf":   float(bear_engulf),
        "atr_score":     atr_score,
        "close_pos":     close_pos,
    }


def _zero() -> dict[str, float]:
    return {
        "bull_score": 50.0, "bear_score": 50.0, "net_score": 0.0,
        "signal_boost": 0.0, "pin_bar": 0.0, "engulfing": 0.0,
        "pin_bull": 0.0, "pin_bear": 0.0,
        "bull_engulf": 0.0, "bear_engulf": 0.0,
        "atr_score": 50.0, "close_pos": 0.5,
    }
