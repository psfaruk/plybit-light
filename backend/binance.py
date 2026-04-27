"""
Crypto historical kline (OHLCV) fetcher via Binance.US.

Binance.com (HTTP 451) and Bybit (HTTP 403) are both geo-blocked from US
cloud datacenters. Binance.US uses the same API schema and works.
"""

import logging
import aiohttp
from config import CANDLE_COUNT

log = logging.getLogger(__name__)

BASE_URL = "https://api.binance.us"

_GRAN_TO_INTERVAL: dict[int, str] = {
    60:    "1m",
    120:   "3m",
    300:   "5m",
    900:   "15m",
    3600:  "1h",
    14400: "4h",
}


async def fetch_history(symbol: str, granularity: int, count: int | None = None) -> list[dict[str, float]]:
    n        = count or CANDLE_COUNT.get(granularity, 200)
    interval = _GRAN_TO_INTERVAL.get(granularity, "1m")
    url      = f"{BASE_URL}/api/v3/klines"
    params   = {"symbol": symbol, "interval": interval, "limit": min(n, 1000)}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    log.error("Binance.US error %d for %s", resp.status, symbol)
                    return []
                raw: list[list[object]] = await resp.json()

        candles: list[dict[str, float]] = []
        for row in raw:
            candles.append({
                "epoch":  float(row[0]) / 1000.0,
                "open":   float(row[1]),
                "high":   float(row[2]),
                "low":    float(row[3]),
                "close":  float(row[4]),
                "volume": float(row[5]),
            })
        return candles
    except Exception as e:
        log.error("Binance.US fetch error for %s: %s", symbol, e)
        return []
