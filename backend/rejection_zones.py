"""
Rejection Zone Engine — tracks repeated price rejections at key levels.

For each price level (rounded to zone_pip), we record:
  - how many times price has touched and rejected
  - the wick size at each touch
  - whether selling/buying pressure is growing
  - a composite grade (S/A/B/C/D)

The grade feeds directly into the signal pipeline as a bonus/filter.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class ZoneTouch:
    epoch: float
    wick_ratio: float      # wick / total_range  0–1
    volume: float
    side: str              # "BUYER" | "SELLER"
    price: float


@dataclass
class RejectionZone:
    level: float
    side: str              # "BUYER" | "SELLER"
    touches: list[ZoneTouch] = field(default_factory=list)
    last_updated: float = 0.0

    # ── derived ─────────────────────────────────────────────
    @property
    def touch_count(self) -> int:
        return len(self.touches)

    @property
    def avg_wick(self) -> float:
        if not self.touches:
            return 0.0
        return float(np.mean([t.wick_ratio for t in self.touches]))

    @property
    def max_wick(self) -> float:
        if not self.touches:
            return 0.0
        return max(t.wick_ratio for t in self.touches)

    @property
    def pressure_growing(self) -> bool:
        """True when each successive rejection wick is ≥ previous."""
        if len(self.touches) < 2:
            return False
        wicks = [t.wick_ratio for t in self.touches[-4:]]
        return all(wicks[i] >= wicks[i - 1] * 0.9 for i in range(1, len(wicks)))

    def score(self) -> float:
        """0–100 composite score."""
        if self.touch_count < 2:
            return 0.0

        freq_pts   = min(self.touch_count * 12, 36)   # max 36
        growth_pts = 28 if self.pressure_growing else 8
        wick_pts   = min(self.avg_wick * 60, 24)       # max 24 (avg_wick 0.4 → 24)
        recency    = 12 if (time.time() - self.last_updated) < 3600 else 0
        return min(freq_pts + growth_pts + wick_pts + recency, 100.0)

    def grade(self) -> str:
        s = self.score()
        if s >= 80: return "S"
        if s >= 65: return "A"
        if s >= 50: return "B"
        if s >= 35: return "C"
        return "D"

    def signal(self) -> str:
        """Trading signal implied by this rejection zone."""
        if self.score() < 50:
            return "NONE"
        return "RED" if self.side == "SELLER" else "GREEN"


# ── Per-pair zone registry ────────────────────────────────────

class RejectionZoneEngine:
    """
    Call `update(df)` on every 1M candle close.
    Query `get_nearby_zones(price)` inside the signal pipeline.
    """

    def __init__(
        self,
        pip_size: float = 0.0001,
        zone_pips: int  = 5,
        max_zones: int  = 200,
        max_age_s: int  = 86_400 * 3,   # 3-day zone expiry
    ) -> None:
        self._pip   = pip_size
        self._zone  = pip_size * zone_pips
        self._max   = max_zones
        self._age   = max_age_s
        self._zones: dict[float, RejectionZone] = {}

    # ── public API ───────────────────────────────────────────

    def update(self, df: pd.DataFrame) -> None:
        """Process the last closed candle and update zone memory."""
        if len(df) < 3:
            return
        self._expire_old()
        self._process_candle(df)

    def get_nearby_zones(
        self,
        price: float,
        radius_pips: int = 10,
    ) -> list[RejectionZone]:
        """Return zones within ±radius_pips of price, sorted by score desc."""
        r = self._pip * radius_pips
        nearby = [z for z in self._zones.values() if abs(z.level - price) <= r]
        return sorted(nearby, key=lambda z: z.score(), reverse=True)

    def best_zone_near(self, price: float, radius_pips: int = 10) -> Optional[RejectionZone]:
        zones = self.get_nearby_zones(price, radius_pips)
        return zones[0] if zones else None

    def summary(self, price: float) -> dict:
        """Compact dict for signal pipeline consumption."""
        zone = self.best_zone_near(price)
        if zone is None or zone.score() < 35:
            return {"rz_score": 0.0, "rz_grade": "D", "rz_signal": "NONE",
                    "rz_touches": 0, "rz_growing": False, "rz_side": "NONE"}
        return {
            "rz_score":   zone.score(),
            "rz_grade":   zone.grade(),
            "rz_signal":  zone.signal(),
            "rz_touches": zone.touch_count,
            "rz_growing": zone.pressure_growing,
            "rz_side":    zone.side,
        }

    # ── internals ────────────────────────────────────────────

    def _round_level(self, price: float) -> float:
        return round(round(price / self._zone) * self._zone, 8)

    def _expire_old(self) -> None:
        cutoff = time.time() - self._age
        self._zones = {k: v for k, v in self._zones.items()
                       if v.last_updated >= cutoff}

    def _process_candle(self, df: pd.DataFrame) -> None:
        c = df.iloc[-1]
        o, h, l, close = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
        vol   = float(c.get("volume", 1.0))
        epoch = float(c.get("epoch", time.time()))
        total = max(h - l, 1e-10)

        upper_wick = h - max(o, close)
        lower_wick = min(o, close) - l
        body       = abs(close - o)
        body_ratio = body / total

        # Seller rejection: significant upper wick, price closed near lows
        if upper_wick / total >= 0.25 and body_ratio < 0.6:
            level = self._round_level(h)
            touch = ZoneTouch(
                epoch=epoch,
                wick_ratio=upper_wick / total,
                volume=vol,
                side="SELLER",
                price=h,
            )
            self._record(level, "SELLER", touch)

        # Buyer rejection: significant lower wick, price closed near highs
        if lower_wick / total >= 0.25 and body_ratio < 0.6:
            level = self._round_level(l)
            touch = ZoneTouch(
                epoch=epoch,
                wick_ratio=lower_wick / total,
                volume=vol,
                side="BUYER",
                price=l,
            )
            self._record(level, "BUYER", touch)

    def _record(self, level: float, side: str, touch: ZoneTouch) -> None:
        if level not in self._zones:
            if len(self._zones) >= self._max:
                # Evict weakest zone
                worst = min(self._zones, key=lambda k: self._zones[k].score())
                del self._zones[worst]
            self._zones[level] = RejectionZone(level=level, side=side)

        z = self._zones[level]
        # Only keep last 10 touches per zone
        z.touches.append(touch)
        if len(z.touches) > 10:
            z.touches = z.touches[-10:]
        z.last_updated = touch.epoch
        # Dominant side wins
        sides = [t.side for t in z.touches]
        z.side = "SELLER" if sides.count("SELLER") >= sides.count("BUYER") else "BUYER"


# ── pip size lookup ───────────────────────────────────────────

_PIP_SIZES: dict[str, float] = {
    "XAUUSD": 0.01,
    "USDJPY": 0.01,
    "EURJPY": 0.01,
    "GBPJPY": 0.01,
}
_DEFAULT_PIP = 0.0001


def make_engine(pair: str) -> RejectionZoneEngine:
    pip = _PIP_SIZES.get(pair, _DEFAULT_PIP)
    return RejectionZoneEngine(pip_size=pip)
