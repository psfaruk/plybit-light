"""
Crypto historical kline (OHLCV) fetcher.

Module name kept as `binance` for compatibility — actually uses Bybit's public
v5 API because Binance.com is geo-blocked from many cloud providers (HTTP 451).
Bybit has no such restriction and supports the same USDT-quoted pairs.
"""

import logging
import aiohttp
from config import CANDLE_COUNT

log = logging.getLogger(__name__)

BASE_URL = "https://api.bybit.com"

# Bybit interval codes for spot klines
_GRAN_TO_INTERVAL: dict[int, str] = {
    60:    "1",
    120:   "3",
    300:   "5",
    900:   "15",
    3600:  "60",
    14400: "240",
}


async def fetch_history(symbol: str, granularity: int, count: int | None = None) -> list[dict[str, float]]:
    n        = count or CANDLE_COUNT.get(granularity, 200)
    interval = _GRAN_TO_INTERVAL.get(granularity, "1")
    url      = f"{BASE_URL}/v5/market/kline"
    params   = {
        "category": "spot",
        "symbol":   symbol,
        "interval": interval,
        "limit":    min(n, 1000),
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    log.error("Bybit error %d for %s", resp.status, symbol)
                    return []
                payload = await resp.json()

        if payload.get("retCode") != 0:
            log.error("Bybit API error for %s: %s", symbol, payload.get("retMsg"))
            return []

        # Bybit returns newest first → reverse for chronological order
        rows = payload.get("result", {}).get("list", [])
        rows.reverse()

        candles: list[dict[str, float]] = []
        for row in rows:
            # row = [start_ms, open, high, low, close, volume, turnover]
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
        log.error("Bybit fetch error for %s: %s", symbol, e)
        return []
