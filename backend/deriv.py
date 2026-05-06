"""Deriv WebSocket API client — historical candle fetching with pagination."""

import asyncio
import json
import logging
import websockets
from config import DERIVE_APP_ID, DERIVE_TOKEN, CANDLE_COUNT

log = logging.getLogger(__name__)

WS_URL = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIVE_APP_ID}"
MAX_PER_REQUEST = 5000   # Deriv API hard limit per ticks_history call


async def fetch_history(
    symbol: str,
    granularity: int,
    count: int | None = None,
) -> list[dict[str, float]]:
    """Single-batch fetch (backwards-compatible). Max 5000 candles."""
    n = count or CANDLE_COUNT.get(granularity, 200)
    n = min(n, MAX_PER_REQUEST)
    try:
        async with websockets.connect(WS_URL, ping_interval=30, close_timeout=5) as ws:
            await ws.send(json.dumps({"authorize": DERIVE_TOKEN}))
            auth = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if auth.get("error"):
                log.error("Deriv auth error: %s", auth["error"])
                return []
            req = {
                "ticks_history":    symbol,
                "adjust_start_time": 1,
                "count":            n,
                "end":              "latest",
                "granularity":      granularity,
                "style":            "candles",
            }
            await ws.send(json.dumps(req))
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if resp.get("error"):
                log.error("Deriv history error for %s: %s", symbol, resp["error"])
                return []
            return _parse(resp.get("candles", []))
    except Exception as e:
        log.error("Deriv fetch error for %s %ds: %s", symbol, granularity, e)
        return []


async def fetch_history_paginated(
    symbol: str,
    granularity: int,
    total_count: int = 10000,
) -> list[dict[str, float]]:
    """
    Paginated fetch — walks backwards in time until total_count candles
    are collected or no more history is available.

    Strategy:
      1. Connect once and keep the WS open for all batches.
      2. First batch: end="latest"  → most recent MAX_PER_REQUEST candles.
      3. Next batches: end = oldest_epoch_seen - 1, until we have enough.
      4. Deduplicate + sort before returning.
    """
    all_candles: list[dict[str, float]] = []
    end_time: str | int = "latest"

    try:
        async with websockets.connect(
            WS_URL, ping_interval=20, ping_timeout=30, close_timeout=5
        ) as ws:
            await ws.send(json.dumps({"authorize": DERIVE_TOKEN}))
            auth = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if auth.get("error"):
                log.error("Deriv auth error: %s", auth["error"])
                return []

            while len(all_candles) < total_count:
                batch_size = min(MAX_PER_REQUEST, total_count - len(all_candles))
                req = {
                    "ticks_history":    symbol,
                    "adjust_start_time": 1,
                    "count":            batch_size,
                    "end":              end_time,
                    "granularity":      granularity,
                    "style":            "candles",
                }
                await ws.send(json.dumps(req))
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))

                if resp.get("error"):
                    log.warning(
                        "Deriv pagination error %s @%s: %s",
                        symbol, end_time, resp["error"].get("message", "")
                    )
                    break

                batch = _parse(resp.get("candles", []))
                if not batch:
                    break  # no more history

                # Prepend older batch in front of what we already have
                all_candles = batch + all_candles

                if len(batch) < batch_size:
                    break  # Deriv returned fewer than requested — we hit the start

                # Next batch ends just before the oldest candle we have
                end_time = int(batch[0]["epoch"]) - 1

                # Small pause to respect Deriv rate limits (~20 req/s)
                await asyncio.sleep(0.15)

    except Exception as e:
        log.error("Deriv paginated fetch error %s %ds: %s", symbol, granularity, e)

    return _dedup_sort(all_candles)


# ── Helpers ──────────────────────────────────────────────────

def _parse(raw: list[dict]) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for c in raw:
        try:
            out.append({
                "epoch": float(c["epoch"]),
                "open":  float(c["open"]),
                "high":  float(c["high"]),
                "low":   float(c["low"]),
                "close": float(c["close"]),
            })
        except (KeyError, ValueError):
            pass
    return out


def _dedup_sort(candles: list[dict[str, float]]) -> list[dict[str, float]]:
    seen: set[float] = set()
    result: list[dict[str, float]] = []
    for c in sorted(candles, key=lambda x: x["epoch"]):
        if c["epoch"] not in seen:
            seen.add(c["epoch"])
            result.append(c)
    return result
