"""Fibonacci harmonic pattern detection (Gartley, Bat, Butterfly, Crab)."""

import numpy as np
import pandas as pd


HARMONIC_DEFS: dict[str, dict[str, object]] = {
    "gartley_bull": {"XA": 0.618, "AB": (0.382, 0.618), "BC": (0.382, 0.886), "CD": (1.13, 1.618), "bull": True},
    "gartley_bear": {"XA": 0.618, "AB": (0.382, 0.618), "BC": (0.382, 0.886), "CD": (1.13, 1.618), "bull": False},
    "bat_bull":     {"XA": (0.382, 0.500), "AB": (0.382, 0.500), "BC": (0.382, 0.886), "CD": (1.618, 2.618), "bull": True},
    "bat_bear":     {"XA": (0.382, 0.500), "AB": (0.382, 0.500), "BC": (0.382, 0.886), "CD": (1.618, 2.618), "bull": False},
    "butterfly_bull":{"XA": 0.786, "AB": (0.382, 0.618), "BC": (0.382, 0.886), "CD": (1.618, 2.618), "bull": True},
    "butterfly_bear":{"XA": 0.786, "AB": (0.382, 0.618), "BC": (0.382, 0.886), "CD": (1.618, 2.618), "bull": False},
    "crab_bull":    {"XA": (0.382, 0.618), "AB": (0.382, 0.618), "BC": (0.382, 0.886), "CD": (2.618, 3.618), "bull": True},
    "crab_bear":    {"XA": (0.382, 0.618), "AB": (0.382, 0.618), "BC": (0.382, 0.886), "CD": (2.618, 3.618), "bull": False},
}

TOL = 0.06  # ±6% tolerance on Fibonacci ratios


def _ratio_ok(ratio: float, target: object) -> bool:
    if isinstance(target, tuple):
        lo, hi = float(target[0]), float(target[1])
        return lo * (1 - TOL) <= ratio <= hi * (1 + TOL)
    t = float(target)  # type: ignore[arg-type]
    return t * (1 - TOL) <= ratio <= t * (1 + TOL)


def _swing_pivots(df: pd.DataFrame, n: int = 5) -> list[float]:
    """Return alternating swing high/low prices using actual H/L candle data."""
    pivots: list[float] = []
    for i in range(n, len(df) - n):
        h_win = df["high"].iloc[i - n: i + n + 1]
        l_win = df["low"].iloc[i - n: i + n + 1]
        if float(df["high"].iloc[i]) == float(h_win.max()):
            pivots.append(float(df["high"].iloc[i]))
        elif float(df["low"].iloc[i]) == float(l_win.min()):
            pivots.append(float(df["low"].iloc[i]))
    return pivots[-10:]


def detect_harmonics(df: pd.DataFrame) -> dict[str, int]:
    result: dict[str, int] = {k: 0 for k in HARMONIC_DEFS}
    if len(df) < 30:
        return result

    pivots = _swing_pivots(df, 3)
    if len(pivots) < 5:
        return result

    # Use last 5 pivots as X, A, B, C, D
    X, A, B, C, D = pivots[-5], pivots[-4], pivots[-3], pivots[-2], pivots[-1]

    xa = abs(A - X)
    ab = abs(B - A)
    bc = abs(C - B)
    cd = abs(D - C)

    if xa < 1e-10:
        return result

    xa_r = xa / xa           # always 1, reference
    ab_r = ab / (xa + 1e-10)
    bc_r = bc / (ab + 1e-10)
    cd_r = cd / (bc + 1e-10)

    bull = D < C  # D below C = potential bullish reversal at D
    bear = D > C  # D above C = potential bearish reversal at D

    for name, dfn in HARMONIC_DEFS.items():
        is_bull = bool(dfn["bull"])
        if is_bull and not bull:
            continue
        if not is_bull and not bear:
            continue
        if (_ratio_ok(ab_r, dfn["AB"]) and
                _ratio_ok(bc_r, dfn["BC"]) and
                _ratio_ok(cd_r, dfn["CD"])):
            result[name] = 1

    return result


def harmonic_confidence_boost(harmonics: dict[str, int], direction: str) -> float:
    """Return confidence boost if a harmonic pattern matches the signal direction."""
    bull_patterns = ["gartley_bull", "bat_bull", "butterfly_bull", "crab_bull"]
    bear_patterns = ["gartley_bear", "bat_bear", "butterfly_bear", "crab_bear"]

    if direction == "GREEN" and any(harmonics.get(p, 0) for p in bull_patterns):
        return 0.05
    if direction == "RED" and any(harmonics.get(p, 0) for p in bear_patterns):
        return 0.05
    return 0.0
