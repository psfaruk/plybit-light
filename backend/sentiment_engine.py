"""
Sentiment Engine — Quotex Traders Mood + Contrarian Logic.

Quotex broadcasts the real-time sentiment of all platform traders for each
asset.  Payload shape (observed):
    {"asset": "EURUSD_otc", "value": 0.62, ...}
where `value` is the share of buyers (0..1) and 1 - value = sellers.

THE KEY INSIGHT — Retail Trader Contrarian:
Quotex platform is 90%+ retail traders.  Retail crowd is consistently wrong
at extremes.  Therefore:
    > 70% buyers  → strong SELL bias (crowd long, smart money short)
    < 30% buyers  → strong BUY  bias (crowd short, smart money long)
    40-60%        → neutral / no edge

Sentiment SHIFT matters more than absolute level:
    fast move from 60% → 80% buyers in 5 minutes = euphoric top → SELL hard
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any


class SentimentEngine:
    """
    Per-pair sentiment tracker.

    Usage:
        eng = SentimentEngine()
        eng.update(raw_mood_dict)                 # called from background poller
        score = eng.score(direction="GREEN")      # called from signal pipeline
    """

    def __init__(self, history_size: int = 60) -> None:
        # history of (epoch, buyer_ratio) — last `history_size` samples
        self._history: deque[tuple[float, float]] = deque(maxlen=history_size)
        self._last_value: float = 0.5
        self._last_epoch: float = 0.0

    # ── ingest ───────────────────────────────────────────────

    def update(self, raw: dict | None) -> None:
        if not raw or not isinstance(raw, dict):
            return
        # Quotex payload: { "value": <0..1>, ... } — buyer ratio
        v = raw.get("value", raw.get("up", raw.get("mood")))
        if v is None:
            return
        try:
            value = float(v)
        except (TypeError, ValueError):
            return
        # Normalise — sometimes server sends % (0..100)
        if value > 1.0:
            value = value / 100.0
        value = max(0.0, min(value, 1.0))
        self._history.append((time.time(), value))
        self._last_value = value
        self._last_epoch = time.time()

    # ── output ───────────────────────────────────────────────

    def is_fresh(self, max_age_s: int = 120) -> bool:
        return (time.time() - self._last_epoch) <= max_age_s

    @property
    def buyer_ratio(self) -> float:
        return self._last_value

    @property
    def crowd_extreme(self) -> str:
        """One of EUPHORIC_TOP, DESPAIR_BOTTOM, NEUTRAL."""
        if self._last_value >= 0.75:
            return "EUPHORIC_TOP"
        if self._last_value <= 0.25:
            return "DESPAIR_BOTTOM"
        return "NEUTRAL"

    @property
    def velocity(self) -> float:
        """Change in buyer_ratio over last 5 minutes (-1..+1)."""
        if len(self._history) < 5:
            return 0.0
        now = time.time()
        recent_5min = [v for t, v in self._history if now - t <= 300]
        if len(recent_5min) < 2:
            return 0.0
        return float(recent_5min[-1] - recent_5min[0])

    def score(self, direction: str) -> dict[str, Any]:
        """
        Return a confidence multiplier + reason for the proposed direction.
        `direction` is "GREEN" (CALL) or "RED" (PUT).
        """
        if not self.is_fresh():
            return {"sent_mult": 1.0, "sent_align": "STALE",
                    "buyer_ratio": self._last_value, "reason": "stale"}

        br        = self._last_value
        extreme   = self.crowd_extreme
        velocity  = self.velocity

        # Contrarian logic — crowd extreme = fade the crowd
        if extreme == "EUPHORIC_TOP":     # too many buyers
            if direction == "RED":
                mult = 1.15   # crowd long, we go short — strong edge
                align = "CONTRARIAN_EDGE"
            else:
                mult = 0.82
                align = "TRAPPING_CROWD"
        elif extreme == "DESPAIR_BOTTOM": # too many sellers
            if direction == "GREEN":
                mult = 1.15
                align = "CONTRARIAN_EDGE"
            else:
                mult = 0.82
                align = "TRAPPING_CROWD"
        else:
            # In neutral zone — sentiment shift is what matters
            if velocity > 0.10 and direction == "RED":
                mult = 1.05   # buyers piling in = warning sign
                align = "BUYER_TRAP_SETUP"
            elif velocity < -0.10 and direction == "GREEN":
                mult = 1.05   # sellers piling in = capitulation
                align = "SELLER_CAPITULATION"
            elif (br > 0.55 and direction == "GREEN") or (br < 0.45 and direction == "RED"):
                # Mild crowd agreement — slight penalty (you're not contrarian)
                mult = 0.97
                align = "WITH_CROWD"
            else:
                mult = 1.00
                align = "NEUTRAL"

        return {
            "sent_mult":   mult,
            "sent_align":  align,
            "buyer_ratio": round(br, 3),
            "velocity":    round(velocity, 3),
            "extreme":     extreme,
        }


# ── Order size / volume distribution engine ───────────────────

class OrderSizeEngine:
    """
    Parses Quotex's `all_size` payload — a distribution of order sizes across
    price buckets.  Useful for detecting institutional (large) vs retail
    (small) flow.

    Payload shape varies; treat defensively.
    """

    def __init__(self) -> None:
        self._last_payload: dict | None = None
        self._last_epoch: float = 0.0

    def update(self, raw: dict | None) -> None:
        if not raw or not isinstance(raw, dict):
            return
        self._last_payload = raw
        self._last_epoch   = time.time()

    def is_fresh(self, max_age_s: int = 60) -> bool:
        return (time.time() - self._last_epoch) <= max_age_s

    def features(self) -> dict[str, Any]:
        """Return defensive dict; pipeline can skip if all zeros."""
        if not self.is_fresh() or self._last_payload is None:
            return {"os_total": 0.0, "os_buy_pct": 0.5, "os_imbalance": 0.0}

        p = self._last_payload
        # Quotex sends an OHLCV-like structure with `up_volume` / `down_volume`
        # plus aggregated buy/sell sizes.  Field names are not stable so we
        # probe a few common variants.
        buy  = float(p.get("buy",  p.get("up_volume",   p.get("up",  0))) or 0)
        sell = float(p.get("sell", p.get("down_volume", p.get("down", 0))) or 0)
        total = buy + sell

        if total <= 0:
            return {"os_total": 0.0, "os_buy_pct": 0.5, "os_imbalance": 0.0}

        buy_pct  = buy / total
        imbalance = (buy - sell) / total  # -1..+1
        return {
            "os_total":     round(total, 2),
            "os_buy_pct":   round(buy_pct, 3),
            "os_imbalance": round(imbalance, 3),
        }
