"""Advanced institutional features: Wyckoff, VSA, Elliott Wave, Supply/Demand."""

import numpy as np
import pandas as pd


def _pivots(series: pd.Series, n: int = 5) -> list[tuple[int, float]]:
    pts: list[tuple[int, float]] = []
    for i in range(n, len(series) - n):
        window = series.iloc[i - n: i + n + 1]
        if series.iloc[i] == window.max() or series.iloc[i] == window.min():
            pts.append((i, float(series.iloc[i])))
    return pts


def compute_wyckoff(df: pd.DataFrame) -> dict[str, int]:
    if len(df) < 50:
        return {k: 0 for k in _wyckoff_keys()}

    c   = df["close"]
    h   = df["high"]
    l   = df["low"]
    vol = df.get("volume", pd.Series(np.ones(len(df)), index=df.index))

    # 50-bar range
    rng_h = h.tail(50).max()
    rng_l = l.tail(50).min()
    rng   = rng_h - rng_l + 1e-10
    price = float(c.iloc[-1])

    # Selling Climax: very high volume + big bear candle near low
    vol_avg  = vol.rolling(20).mean().iloc[-1]
    last_vol = float(vol.iloc[-1])
    last_rng = float(h.iloc[-1] - l.iloc[-1])
    avg_rng  = float((h - l).rolling(20).mean().iloc[-1])

    sc  = int(last_vol > vol_avg * 2.5 and float(c.iloc[-1]) < float(c.iloc[-2]) and price < rng_l + rng * 0.2)
    ar  = int(float(c.iloc[-1]) > float(c.iloc[-3]) and price < rng_l + rng * 0.4)
    spring     = int(float(l.iloc[-1]) < rng_l and float(c.iloc[-1]) > rng_l)
    upthrust   = int(float(h.iloc[-1]) > rng_h and float(c.iloc[-1]) < rng_h)
    sos        = int(price > rng_h and float(c.iloc[-1]) > float(c.iloc[-5]))
    sow        = int(price < rng_l and float(c.iloc[-1]) < float(c.iloc[-5]))
    markup     = int(price > rng_h * 0.9 and float(c.iloc[-5]) < float(c.iloc[-1]))
    markdown   = int(price < rng_l + rng * 0.1 and float(c.iloc[-5]) > float(c.iloc[-1]))
    accum      = int(rng < avg_rng * 0.5 and price < rng_l + rng * 0.35)
    distrib    = int(rng < avg_rng * 0.5 and price > rng_h - rng * 0.35)

    return {
        "wyckoff_accumulation": accum,
        "wyckoff_distribution": distrib,
        "wyckoff_markup":       markup,
        "wyckoff_markdown":     markdown,
        "wyckoff_spring":       spring,
        "wyckoff_upthrust":     upthrust,
        "wyckoff_sos":          sos,
        "wyckoff_sow":          sow,
        "wyckoff_sc":           sc,
        "wyckoff_ar":           ar,
    }


def compute_elliott_wave(df: pd.DataFrame) -> dict[str, float]:
    if len(df) < 30:
        return {k: 0.0 for k in _elliott_keys()}

    c = df["close"]
    pts = _pivots(c, 3)
    if len(pts) < 5:
        return {k: 0.0 for k in _elliott_keys()}

    # Simple wave ratio check using last 5 pivots
    vals = [p[1] for p in pts[-5:]]
    diffs = [vals[i+1] - vals[i] for i in range(len(vals)-1)]
    if len(diffs) < 4:
        return {k: 0.0 for k in _elliott_keys()}

    # Impulse detection: alternating direction, wave3 > wave1
    w1, w2, w3, w4 = diffs[0], diffs[1], diffs[2], diffs[3]
    impulse_bull = int(w1 > 0 and w2 < 0 and w3 > 0 and abs(w3) > abs(w1))
    impulse_bear = int(w1 < 0 and w2 > 0 and w3 < 0 and abs(w3) > abs(w1))

    wave_ratio = abs(w3) / (abs(w1) + 1e-10)
    correction = int(abs(w1) > 0 and abs(w2) > 0 and abs(w3) < abs(w1))

    return {
        "elliott_impulse_bull": float(impulse_bull),
        "elliott_impulse_bear": float(impulse_bear),
        "elliott_correction":   float(correction),
        "elliott_wave_ratio":   float(min(wave_ratio, 5.0)),
        "elliott_wave_count":   float(len(pts) % 5),
    }


def compute_supply_demand(df: pd.DataFrame) -> dict[str, float]:
    if len(df) < 20:
        return {k: 0.0 for k in _sd_keys()}

    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    price = float(c.iloc[-1])
    avg_rng = float((h - l).rolling(10).mean().iloc[-1])

    # Drop-Base-Rally: find sharp drop, then tight base, then sharp rally
    def _dbr(lookback: int = 30) -> float:
        sub = df.tail(lookback)
        drops   = ((sub["close"] - sub["open"]) / avg_rng < -1.5).sum()
        rallies = ((sub["close"] - sub["open"]) / avg_rng >  1.5).sum()
        return float(drops > 0 and rallies > drops)

    def _rbd(lookback: int = 30) -> float:
        sub = df.tail(lookback)
        drops   = ((sub["close"] - sub["open"]) / avg_rng < -1.5).sum()
        rallies = ((sub["close"] - sub["open"]) / avg_rng >  1.5).sum()
        return float(rallies > 0 and drops > rallies)

    demand = _dbr()
    supply = _rbd()

    rng_low   = float(l.tail(20).min())
    rng_high  = float(h.tail(20).max())
    at_demand = float(price < rng_low + (rng_high - rng_low) * 0.25)
    at_supply = float(price > rng_high - (rng_high - rng_low) * 0.25)

    # Zone strength (0-10): higher if price departed strongly
    departure = float((c - o).abs().tail(5).mean()) / (avg_rng + 1e-10)
    zone_strength = min(departure * 5, 10.0)
    zone_fresh    = float(not bool((l.tail(5) < rng_low * 1.001).any()))

    return {
        "sd_demand_zone":  demand,
        "sd_supply_zone":  supply,
        "sd_at_demand":    at_demand,
        "sd_at_supply":    at_supply,
        "sd_zone_strength": zone_strength,
        "sd_zone_fresh":   zone_fresh,
    }


def _wyckoff_keys() -> list[str]:
    return [
        "wyckoff_accumulation", "wyckoff_distribution", "wyckoff_markup",
        "wyckoff_markdown", "wyckoff_spring", "wyckoff_upthrust",
        "wyckoff_sos", "wyckoff_sow", "wyckoff_sc", "wyckoff_ar",
    ]


def _elliott_keys() -> list[str]:
    return [
        "elliott_impulse_bull", "elliott_impulse_bear",
        "elliott_correction", "elliott_wave_ratio", "elliott_wave_count",
    ]


def _sd_keys() -> list[str]:
    return [
        "sd_demand_zone", "sd_supply_zone", "sd_at_demand",
        "sd_at_supply", "sd_zone_strength", "sd_zone_fresh",
    ]


def compute_all_advanced(df: pd.DataFrame) -> dict[str, float]:
    out: dict[str, float] = {}
    for d in [compute_wyckoff(df), compute_elliott_wave(df), compute_supply_demand(df)]:
        out.update({k: float(v) for k, v in d.items()})
    return out
