"""High-impact news calendar from ForexFactory (cached)."""

import asyncio
import logging
import time
from datetime import datetime, timezone
import aiohttp

log = logging.getLogger(__name__)

_cache: list[dict[str, object]] = []
_cache_ts: float = 0.0
_CACHE_TTL = 3600  # refresh every 1 hour

NEWS_BLOCK_WINDOW = 15 * 60  # ±15 minutes


async def fetch_news_events() -> list[dict[str, object]]:
    """Fetch high-impact news events. Returns list of {time_utc, currency, impact, title}."""
    global _cache, _cache_ts

    now = time.time()
    if _cache and now - _cache_ts < _CACHE_TTL:
        return _cache

    try:
        # ForexFactory JSON calendar (unofficial endpoint)
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return _cache
                data: list[dict[str, object]] = await resp.json(content_type=None)

        events: list[dict[str, object]] = []
        for item in data:
            impact = str(item.get("impact", "")).lower()
            if impact not in ("high", "medium"):
                continue
            date_str = str(item.get("date", ""))
            time_str = str(item.get("time", ""))
            try:
                dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %I:%M%p")
                dt = dt.replace(tzinfo=timezone.utc)
                events.append({
                    "time_utc": dt.timestamp(),
                    "currency": item.get("country", ""),
                    "impact":   impact,
                    "title":    item.get("title", ""),
                })
            except Exception:
                pass

        _cache    = events
        _cache_ts = now
        return events
    except Exception as e:
        log.warning("News fetch error: %s", e)
        return _cache


def is_news_window(pair: str, utc_ts: float | None = None) -> bool:
    """Returns True if we're within ±15 min of a high-impact event for this pair."""
    ts = utc_ts or time.time()

    # Extract currencies from pair name
    pair_upper = pair.upper().replace("FRX", "").replace("USDT", "")
    currencies = [pair_upper[:3], pair_upper[3:6]] if len(pair_upper) >= 6 else []

    for event in _cache:
        event_ts = float(event.get("time_utc", 0))
        currency = str(event.get("currency", ""))
        if abs(ts - event_ts) <= NEWS_BLOCK_WINDOW:
            if not currencies or currency in currencies or currency == "USD":
                return True
    return False
