"""Multi-Timeframe Analysis (1H → 15M → 5M → 2M → 1M signal)."""

import numpy as np
import pandas as pd
from config import MTF_WEIGHTS


TF_LABELS = {3600: "1h", 900: "15m", 300: "5m", 120: "2m", 60: "1m"}


def analyze_tf(df: pd.DataFrame) -> dict[str, object]:
    """Determine bias for a single timeframe."""
    if len(df) < 10:
        return {"bias": "neutral", "strength": 0.0}

    c   = df["close"]
    h   = df["high"]
    l   = df["low"]
    ema20 = c.ewm(span=20, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()

    price = float(c.iloc[-1])
    e20   = float(ema20.iloc[-1])
    e50   = float(ema50.iloc[-1])

    # EMA slope
    slope20 = float(ema20.diff(3).iloc[-1] / (price + 1e-10))

    # BOS check
    rng_h = float(h.tail(20).max())
    rng_l = float(l.tail(20).min())
    bos_bull = price > rng_h * 0.98
    bos_bear = price < rng_l * 1.02

    if e20 > e50 and slope20 > 0:
        bias = "bull"
        strength = min(abs(slope20) * 1000 + 0.5, 1.0)
    elif e20 < e50 and slope20 < 0:
        bias = "bear"
        strength = min(abs(slope20) * 1000 + 0.5, 1.0)
    else:
        bias = "neutral"
        strength = 0.3

    if bos_bull and bias == "bull":
        strength = min(strength + 0.2, 1.0)
    if bos_bear and bias == "bear":
        strength = min(strength + 0.2, 1.0)

    return {
        "bias":     bias,
        "strength": strength,
        "ema20":    e20,
        "ema50":    e50,
        "bos_bull": bos_bull,
        "bos_bear": bos_bear,
    }


def compute_mtf_agreement(
    candles: dict[int, pd.DataFrame],
    utc_hour: int = 0,
) -> dict[str, object]:
    """
    Compute top-down MTF agreement.
    candles: {granularity_seconds: DataFrame}
    Returns overall direction, agreement score, and per-TF breakdown.
    """
    tf_results: dict[str, object] = {}
    bull_w = 0.0
    bear_w = 0.0
    total_w = sum(MTF_WEIGHTS.values())

    for gran, label in [(3600, "1h"), (900, "15m"), (300, "5m"), (120, "2m")]:
        df = candles.get(gran)
        if df is None or len(df) < 10:
            tf_results[label] = {"bias": "neutral", "strength": 0.0}
            continue
        res = analyze_tf(df)
        tf_results[label] = res
        w = MTF_WEIGHTS.get(label, 0.15)
        if res["bias"] == "bull":
            bull_w += w * float(res["strength"])
        elif res["bias"] == "bear":
            bear_w += w * float(res["strength"])

    # Killzone bonus
    in_kz   = utc_hour in range(7, 10) or utc_hour in range(12, 16)
    kz_name = "london" if utc_hour in range(7, 10) else ("overlap" if utc_hour in range(12, 16) else "none")

    if bull_w > bear_w:
        direction  = "bull"
        agreement  = bull_w / (total_w + 1e-10)
    elif bear_w > bull_w:
        direction  = "bear"
        agreement  = bear_w / (total_w + 1e-10)
    else:
        direction  = "neutral"
        agreement  = 0.3

    # Check 1H → 15M alignment (critical filter)
    h1_bias  = str(tf_results.get("1h", {}).get("bias", "neutral"))  # type: ignore[union-attr]
    m15_bias = str(tf_results.get("15m", {}).get("bias", "neutral")) # type: ignore[union-attr]
    aligned  = h1_bias == m15_bias and h1_bias != "neutral"

    return {
        "direction":   direction,
        "agreement":   agreement,
        "aligned":     aligned,
        "killzone":    kz_name,
        "in_killzone": in_kz,
        "tfs":         tf_results,
        "bull_weight": bull_w,
        "bear_weight": bear_w,
    }


def mtf_score_pts(mtf: dict[str, object], direction: str = "GREEN") -> float:
    """Convert MTF result to scoring layer points (max 45).

    Points awarded only when each TF bias aligns with the SIGNAL direction
    (bull → GREEN, bear → RED), not just internal MTF consensus.
    """
    pts = 0.0
    tfs = mtf.get("tfs", {})
    aligned_bias = "bull" if direction == "GREEN" else "bear"

    if isinstance(tfs, dict):
        if isinstance(tfs.get("1h"),  dict) and tfs["1h"].get("bias")  == aligned_bias:  # type: ignore[union-attr]
            pts += 12
        if isinstance(tfs.get("15m"), dict) and tfs["15m"].get("bias") == aligned_bias:  # type: ignore[union-attr]
            pts += 10
        if isinstance(tfs.get("5m"),  dict) and tfs["5m"].get("bias")  == aligned_bias:  # type: ignore[union-attr]
            pts += 10
        if isinstance(tfs.get("2m"),  dict) and tfs["2m"].get("bias")  == aligned_bias:  # type: ignore[union-attr]
            pts += 8

    if mtf.get("in_killzone"):
        pts += 5

    return pts
