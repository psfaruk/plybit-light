"""
Price Hold Detection — identifies when price stays at same level for 2-3 seconds.

When a big trader places a large order, price absorbs at one level momentarily
before breaking. This "hold" pattern is an institutional footprint.

Plan rule: "Price যদি ২-৩ সেকেন্ড একই দামে আটকে থাকে → Big Trader এর Order আছে"
"""
from __future__ import annotations

from tick_store import TickBuffer


def analyze_price_hold(
    buf: TickBuffer,
    window_sec: float = 3.0,
    tolerance_pct: float = 0.0002,  # 0.02% — adapts to any instrument price level
) -> dict[str, float]:
    """
    Analyse ticks in the last `window_sec` seconds for a price hold.

    Returns:
        price_hold_active  : 1.0 if hold detected, else 0.0
        hold_duration_sec  : seconds price held (0 if none)
        hold_strength      : 0–100 confidence (100 = full window held)
        price_range_pct    : % price range over window (lower = tighter hold)
        tick_velocity      : price change/sec after hold (+ = bullish break, - = bearish)
        tick_count         : number of ticks in the window
    """
    if buf.buffered < 2:
        return _zero()

    now = buf.last_epoch
    ticks = buf.since(now - window_sec)

    if len(ticks) < 2:
        return _zero()

    prices = [p for _, p in ticks]
    epochs = [e for e, _ in ticks]

    price_mid   = sum(prices) / len(prices)
    price_range = max(prices) - min(prices)
    tolerance   = price_mid * tolerance_pct

    hold_active = price_range <= tolerance

    if hold_active:
        duration = epochs[-1] - epochs[0]
        # 2 s hold = 67%, 3 s hold = 100%
        strength = min(duration / window_sec * 100.0, 100.0)
    else:
        duration = 0.0
        strength = 0.0

    # Velocity: price change per second (reveals break direction after hold)
    dt = epochs[-1] - epochs[0]
    velocity = ((prices[-1] - prices[0]) / dt) if dt > 0 else 0.0

    return {
        "price_hold_active": float(int(hold_active)),
        "hold_duration_sec": float(duration),
        "hold_strength":     float(strength),
        "price_range_pct":   float(price_range / (price_mid + 1e-10) * 100.0),
        "tick_velocity":     float(velocity),
        "tick_count":        float(len(ticks)),
    }


def _zero() -> dict[str, float]:
    return {
        "price_hold_active": 0.0,
        "hold_duration_sec": 0.0,
        "hold_strength":     0.0,
        "price_range_pct":   0.0,
        "tick_velocity":     0.0,
        "tick_count":        0.0,
    }
