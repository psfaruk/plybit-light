"""Pre-close timing engine — samples 20× in the 10s before candle close."""

import asyncio
import time
from typing import Callable, Awaitable
from config import PRE_CLOSE_WINDOW_SEC, PRE_CLOSE_MAX_SAMPLES, PRE_CLOSE_INTERVAL


async def run_pre_close_sampling(
    candle_close_epoch: int,
    sample_fn: Callable[[], Awaitable[dict[str, object]]],
) -> list[dict[str, object]]:
    """
    Start sampling PRE_CLOSE_WINDOW_SEC before candle close.
    Calls sample_fn every PRE_CLOSE_INTERVAL seconds up to PRE_CLOSE_MAX_SAMPLES times.
    Returns list of sample results.
    """
    samples: list[dict[str, object]] = []
    start_at = candle_close_epoch - PRE_CLOSE_WINDOW_SEC

    now = time.time()
    wait = start_at - now
    if wait > 0:
        await asyncio.sleep(wait)

    for i in range(PRE_CLOSE_MAX_SAMPLES):
        try:
            result = await sample_fn()
            result["sample_idx"] = i
            result["ts"]         = time.time()
            samples.append(result)
        except Exception:
            pass

        if i < PRE_CLOSE_MAX_SAMPLES - 1:
            await asyncio.sleep(PRE_CLOSE_INTERVAL)

    return samples


def aggregate_samples(samples: list[dict[str, object]]) -> dict[str, object]:
    """
    Aggregate 20 samples into a single signal.
    Recent 5 samples weighted 60%, first 15 weighted 40%.
    """
    if not samples:
        return {"direction": "SKIP", "confidence": 0.0}

    directions  = [str(s.get("direction", "SKIP")) for s in samples]
    confidences = [float(s.get("confidence", 0.0)) for s in samples]

    bull_votes = directions.count("GREEN")
    bear_votes = directions.count("RED")
    dominant   = "GREEN" if bull_votes >= bear_votes else "RED"

    # Weighted confidence: recent 5 × 0.60, early 15 × 0.40
    recent5 = confidences[-5:]  if len(confidences) >= 5  else confidences
    early15 = confidences[:-5]  if len(confidences) > 5   else []

    recent_avg = sum(recent5) / len(recent5) if recent5 else 0.0
    early_avg  = sum(early15) / len(early15) if early15 else recent_avg
    final_conf = recent_avg * 0.60 + early_avg * 0.40

    # Adjust by vote ratio
    dominant_votes = max(bull_votes, bear_votes)
    vote_ratio     = dominant_votes / max(len(samples), 1)
    final_conf     = final_conf * (0.60 + 0.40 * vote_ratio)

    return {
        "direction":    dominant,
        "confidence":   float(min(final_conf, 0.99)),
        "bull_votes":   bull_votes,
        "bear_votes":   bear_votes,
        "vote_ratio":   vote_ratio,
        "n_samples":    len(samples),
    }
