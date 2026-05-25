"""
Adaptive Confidence Threshold — per-pair, per-session, per-volatility.

Replaces the static GRADE_MODERATE = 0.45 cutoff with a learned threshold
based on actual win rate. The base threshold remains a floor; we only
RAISE it when win rate is poor, never lower it below the configured floor.
"""

from __future__ import annotations

import logging
from typing import Any
import config
import candle_db

log = logging.getLogger(__name__)

BASE_FLOOR        = float(config.GRADE_MODERATE)   # 0.45
MIN_SAMPLES       = 8
TARGET_WIN_RATE   = 0.62      # we want to keep ≥ this win rate
MAX_THRESHOLD     = 0.85
STEP_UP           = 0.03      # raise threshold by this when WR < target
STEP_DOWN         = 0.01      # gently lower when WR > target


def _session_of(utc_hour: int) -> str:
    """Trading session for a given UTC hour."""
    if 8 <= utc_hour < 13:  return "LONDON"
    if 13 <= utc_hour < 17: return "OVERLAP"     # London-NY overlap
    if 17 <= utc_hour < 21: return "NEW_YORK"
    return "ASIAN"


def _vol_bucket(adx: float) -> str:
    """Volatility bucket from ADX."""
    if adx < 18:   return "CHOP"
    if adx < 28:   return "TREND"
    return "STRONG_TREND"


class AdaptiveThreshold:
    """
    Per-key threshold tracker. State is persisted to SQLite so restarts
    keep learned thresholds.  Key = (pair, session, vol_bucket).
    """

    def __init__(self) -> None:
        # key → {threshold, wins, losses}
        self._state: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._loaded = False   # lazy-load on first access

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        try:
            self._state = candle_db.load_adaptive_threshold()
            log.info("AdaptiveThreshold: loaded %d keys from SQLite", len(self._state))
        except Exception as e:
            log.warning("AdaptiveThreshold: could not load from SQLite: %s", e)
        self._loaded = True

    def get_threshold(self, pair: str, utc_hour: int, adx: float) -> float:
        self._ensure_loaded()
        key = (pair, _session_of(utc_hour), _vol_bucket(adx))
        return float(self._state.get(key, {}).get("threshold", BASE_FLOOR))

    def record_outcome(
        self,
        pair: str,
        utc_hour: int,
        adx: float,
        outcome: str,   # "WIN" | "LOSS"
    ) -> None:
        if outcome not in ("WIN", "LOSS"):
            return
        self._ensure_loaded()
        session    = _session_of(utc_hour)
        vol_bkt    = _vol_bucket(adx)
        key        = (pair, session, vol_bkt)
        s = self._state.setdefault(key, {"threshold": BASE_FLOOR, "wins": 0, "losses": 0})
        if outcome == "WIN":
            s["wins"] += 1
        else:
            s["losses"] += 1

        total = s["wins"] + s["losses"]
        if total >= MIN_SAMPLES:
            wr  = s["wins"] / total
            thr = float(s["threshold"])
            if wr < TARGET_WIN_RATE:
                thr = min(thr + STEP_UP, MAX_THRESHOLD)
            elif wr > TARGET_WIN_RATE + 0.05:
                thr = max(thr - STEP_DOWN, BASE_FLOOR)
            s["threshold"] = thr
            if total % 5 == 0:
                log.info("AdaptiveThreshold %s: wr=%.2f n=%d → thr=%.2f",
                         key, wr, total, thr)

        # Persist every update
        try:
            candle_db.save_adaptive_threshold_key(
                pair, session, vol_bkt,
                float(s["threshold"]), int(s["wins"]), int(s["losses"]),
            )
        except Exception as e:
            log.debug("AdaptiveThreshold persist error: %s", e)

    def snapshot(self) -> dict[str, Any]:
        return {
            f"{k[0]}|{k[1]}|{k[2]}": {
                "threshold": round(v["threshold"], 3),
                "n":         v["wins"] + v["losses"],
                "wr":        round(v["wins"] / (v["wins"] + v["losses"]), 3)
                             if (v["wins"] + v["losses"]) > 0 else None,
            }
            for k, v in self._state.items()
        }


adaptive_threshold = AdaptiveThreshold()
