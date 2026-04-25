"""HMM-based market regime detection (trend / range / volatile)."""

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler


class RegimeDetector:
    """
    Simple regime detector using rolling statistics.
    Falls back to rule-based when insufficient data.
    """

    def detect(self, df: pd.DataFrame) -> dict[str, object]:
        if len(df) < 20:
            return {"regime": "unknown", "regime_score": 0.5}

        c   = df["close"]
        h   = df["high"]
        l   = df["low"]
        atr = (h - l).rolling(14).mean().iloc[-1]
        atr_avg = (h - l).rolling(50).mean().iloc[-1] if len(df) >= 50 else atr

        # ADX for trend strength
        plus_dm  = h.diff().clip(lower=0)
        minus_dm = (-l.diff()).clip(lower=0)
        mask  = plus_dm < minus_dm; plus_dm[mask] = 0
        mask2 = minus_dm < plus_dm; minus_dm[mask2] = 0
        plus_di  = 100 * plus_dm.rolling(14).mean()  / (atr + 1e-10)
        minus_di = 100 * minus_dm.rolling(14).mean() / (atr + 1e-10)
        dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
        adx = float(dx.rolling(14).mean().iloc[-1])

        atr_ratio = float(atr / (atr_avg + 1e-10))

        if adx > 25 and atr_ratio < 1.5:
            regime = "trend"
            score  = min(adx / 50, 1.0)
        elif atr_ratio > 1.8:
            regime = "volatile"
            score  = min(atr_ratio / 3.0, 1.0)
        else:
            regime = "range"
            score  = 1.0 - min(adx / 30, 1.0)

        return {
            "regime":       regime,
            "regime_score": score,
            "adx":          adx,
            "atr_ratio":    atr_ratio,
        }
