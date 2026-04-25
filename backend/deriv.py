"""Deriv WebSocket API client for historical candle fetching."""

import asyncio
import json
import logging
import websockets
from config import DERIVE_APP_ID, DERIVE_TOKEN, CANDLE_COUNT

log = logging.getLogger(__name__)

WS_URL = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIVE_APP_ID}"


async def fetch_history(symbol: str, granularity: int, count: int | None = None) -> list[dict[str, float]]:
    """
    Fetch historical OHLC candles from Deriv API.
    Returns list of {epoch, open, high, low, close} dicts.
    """
    n = count or CANDLE_COUNT.get(granularity, 200)
    try:
        async with websockets.connect(WS_URL, ping_interval=30) as ws:
            # Authorize
            await ws.send(json.dumps({"authorize": DERIVE_TOKEN}))
            auth_resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if auth_resp.get("error"):
                log.error("Deriv auth error: %s", auth_resp["error"])
                return []

            # Request history
            req = {
                "ticks_history": symbol,
                "adjust_start_time": 1,
                "count": n,
                "end": "latest",
                "granularity": granularity,
                "style": "candles",
            }
            await ws.send(json.dumps(req))
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))

            if resp.get("error"):
                log.error("Deriv history error for %s: %s", symbol, resp["error"])
                return []

            candles_raw = resp.get("candles", [])
            candles: list[dict[str, float]] = []
            for c in candles_raw:
                candles.append({
                    "epoch": float(c["epoch"]),
                    "open":  float(c["open"]),
                    "high":  float(c["high"]),
                    "low":   float(c["low"]),
                    "close": float(c["close"]),
                })
            return candles
    except Exception as e:
        log.error("Deriv fetch error for %s %ds: %s", symbol, granularity, e)
        return []
