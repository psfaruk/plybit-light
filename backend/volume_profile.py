"""
Volume Profile Engine — builds a price→volume histogram from OHLCV candles.

Key outputs:
  POC   — Point of Control (highest volume price level)
  HVN   — High Volume Nodes (price levels with > 1.5× average volume)
  LVN   — Low Volume Nodes  (price levels with < 0.5× average volume)
  VAH   — Value Area High  (upper edge of 70% volume zone)
  VAL   — Value Area Low   (lower edge of 70% volume zone)
  zone  — AT_RESISTANCE / AT_SUPPORT / AT_POC / LVN_FAST_MOVE / IN_VALUE_AREA / NEUTRAL
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


class VolumeProfileEngine:
    """
    Usage:
        vp = VolumeProfileEngine()
        result = vp.compute(df_1m, current_price)
    """

    def __init__(
        self,
        levels: int = 50,
        value_area_pct: float = 0.70,
        lookback: int = 500,
    ) -> None:
        self._levels    = levels
        self._va_pct    = value_area_pct
        self._lookback  = lookback

    # ── main entry ───────────────────────────────────────────

    def compute(self, df: pd.DataFrame, current_price: float) -> dict:
        """Return compact dict for the signal pipeline."""
        if len(df) < 20:
            return self._empty()

        df_slice = df.tail(self._lookback).copy()
        profile  = self._build_profile(df_slice)
        if profile is None:
            return self._empty()

        prices, volumes = profile
        poc_idx = int(np.argmax(volumes))
        poc     = float(prices[poc_idx])

        avg_vol = float(np.mean(volumes))
        hvn     = [float(p) for p, v in zip(prices, volumes) if v > avg_vol * 1.5]
        lvn     = [float(p) for p, v in zip(prices, volumes) if v < avg_vol * 0.5]

        vah, val = self._value_area(prices, volumes, poc_idx)
        zone     = self._classify(current_price, poc, hvn, lvn, vah, val)

        # nearest HVN above and below current price
        hvn_above = [p for p in hvn if p > current_price]
        hvn_below = [p for p in hvn if p < current_price]
        nearest_resistance = min(hvn_above, default=vah, key=lambda p: abs(p - current_price))
        nearest_support    = max(hvn_below, default=val, key=lambda p: abs(p - current_price))

        return {
            "vp_poc":    poc,
            "vp_vah":    vah,
            "vp_val":    val,
            "vp_zone":   zone,
            "vp_hvn_resistance": nearest_resistance,
            "vp_hvn_support":    nearest_support,
            "vp_at_poc": abs(current_price - poc) / max(abs(poc) * 0.0001, 1e-8) < 5,
            # True when within 5 pips of POC
        }

    # ── internals ────────────────────────────────────────────

    def _build_profile(
        self, df: pd.DataFrame
    ) -> Optional[tuple[np.ndarray, np.ndarray]]:
        if "volume" not in df.columns:
            df = df.copy()
            df["volume"] = 1.0   # tick-based fallback: equal weight per candle

        price_min = float(df["low"].min())
        price_max = float(df["high"].max())
        if price_max <= price_min:
            return None

        bins   = np.linspace(price_min, price_max, self._levels + 1)
        prices = (bins[:-1] + bins[1:]) / 2
        vols   = np.zeros(self._levels, dtype=float)

        for _, row in df.iterrows():
            lo, hi, vol = float(row["low"]), float(row["high"]), float(row["volume"])
            # distribute candle volume linearly across its price range
            mask   = (prices >= lo) & (prices <= hi)
            n_bins = max(mask.sum(), 1)
            vols[mask] += vol / n_bins

        return prices, vols

    def _value_area(
        self,
        prices: np.ndarray,
        volumes: np.ndarray,
        poc_idx: int,
    ) -> tuple[float, float]:
        target = volumes.sum() * self._va_pct
        accumulated = volumes[poc_idx]
        lo_idx = hi_idx = poc_idx

        while accumulated < target:
            can_go_up   = hi_idx + 1 < len(volumes)
            can_go_down = lo_idx - 1 >= 0

            if can_go_up and can_go_down:
                if volumes[hi_idx + 1] >= volumes[lo_idx - 1]:
                    hi_idx += 1
                    accumulated += volumes[hi_idx]
                else:
                    lo_idx -= 1
                    accumulated += volumes[lo_idx]
            elif can_go_up:
                hi_idx += 1
                accumulated += volumes[hi_idx]
            elif can_go_down:
                lo_idx -= 1
                accumulated += volumes[lo_idx]
            else:
                break

        return float(prices[hi_idx]), float(prices[lo_idx])

    def _classify(
        self,
        price: float,
        poc: float,
        hvn: list[float],
        lvn: list[float],
        vah: float,
        val: float,
    ) -> str:
        pip = price * 0.0001   # 1 pip relative to price

        if abs(price - poc) <= pip * 5:
            return "AT_POC"

        if any(abs(price - h) <= pip * 8 for h in hvn if h > poc):
            return "AT_RESISTANCE"

        if any(abs(price - h) <= pip * 8 for h in hvn if h < poc):
            return "AT_SUPPORT"

        if any(abs(price - lv) <= pip * 8 for lv in lvn):
            return "LVN_FAST_MOVE"

        if val <= price <= vah:
            return "IN_VALUE_AREA"

        if price > vah:
            return "ABOVE_VALUE_AREA"

        return "BELOW_VALUE_AREA"

    @staticmethod
    def _empty() -> dict:
        return {
            "vp_poc":   0.0,
            "vp_vah":   0.0,
            "vp_val":   0.0,
            "vp_zone":  "NEUTRAL",
            "vp_hvn_resistance": 0.0,
            "vp_hvn_support":    0.0,
            "vp_at_poc": False,
        }


# module-level singleton factory
def make_engine() -> VolumeProfileEngine:
    return VolumeProfileEngine()
