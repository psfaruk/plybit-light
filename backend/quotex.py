"""Quotex data connector — uses pyquotex library for reliable access."""

import asyncio
import itertools
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from pyquotex.stable_api import Quotex

import config

log = logging.getLogger(__name__)

# Reconnect delay ladder: 2s → 5s → 10s → 30s → 60s (then stays 60s)
_RECONNECT_DELAYS = [2.0, 5.0, 10.0, 30.0, 60.0]
_KEEPALIVE_SEC    = 2.0   # ping the WS every N seconds to catch silent drops
_SESSION_FILE     = os.path.join(os.path.dirname(__file__), "session.json")

# ── Pair name helpers ─────────────────────────────────────────

LIVE_PAIRS: list[str] = [
    "XAUUSD", "EURUSD", "GBPUSD", "USDJPY",
    "AUDUSD", "USDCAD", "NZDUSD", "USDCHF",
]


def _is_forex_open() -> bool:
    """True when Forex market is live (Sun 21:00 – Fri 21:00 UTC)."""
    dt  = datetime.now(timezone.utc)
    dow = dt.weekday()  # 0=Mon … 6=Sun
    h   = dt.hour
    if dow == 4 and h >= 21:  # Friday after 21:00
        return False
    if dow == 5:               # Saturday all day
        return False
    if dow == 6 and h < 21:   # Sunday before 21:00
        return False
    return True


def get_quotex_name(internal_pair: str) -> str:
    """Return Quotex asset name (live or OTC) based on market hours."""
    if _is_forex_open():
        return internal_pair
    return f"{internal_pair}_otc"


# ── Session token persistence ─────────────────────────────────

def _load_saved_ssid() -> str:
    """Load cached session token from session.json (written after a fresh login)."""
    try:
        if os.path.exists(_SESSION_FILE):
            data = json.loads(open(_SESSION_FILE).read())
            return str(data.get("ssid", ""))
    except Exception:
        pass
    return ""


def _save_ssid(ssid: str) -> None:
    """Persist session token so next startup skips a full login."""
    try:
        existing: dict = {}
        if os.path.exists(_SESSION_FILE):
            try:
                existing = json.loads(open(_SESSION_FILE).read())
            except Exception:
                pass
        existing["ssid"] = ssid
        with open(_SESSION_FILE, "w") as f:
            json.dump(existing, f)
    except Exception as e:
        log.debug("Could not save ssid: %s", e)


# ── Shared client instance ────────────────────────────────────

_client: Quotex | None = None
_req_counter  = itertools.count(1)
_connect_lock = asyncio.Lock()   # prevents concurrent connect() races
_last_connect_ok: float = 0.0    # wall-clock of last confirmed good connection


def _make_client(use_email: bool = False) -> Quotex:
    """
    Create Quotex client.
    - use_email=True  → full email/password login (refreshes session).
    - use_email=False → session-token fast path (skips HTTP login).
    """
    email    = config.QUOTEX_EMAIL    if use_email else "placeholder@quotex.io"
    password = config.QUOTEX_PASSWORD if use_email else "placeholder"

    client = Quotex(
        email=email,
        password=password,
        host="qxbroker.com",
        lang="en",
        root_path=".",
        user_data_dir="browser",
        asset_default="EURUSD",
        period_default=60,
    )

    if not use_email:
        # Pick best available token: env var > saved file
        ssid = config.QUOTEX_SESSION_TOKEN or _load_saved_ssid()
        if ssid:
            client.set_session(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                ssid=ssid,
            )
            log.info("Quotex: using session token %s…", ssid[:8])
        else:
            log.warning("Quotex: no session token — forcing email/password login")
            return _make_client(use_email=True)
    else:
        log.info("Quotex: connecting with email/password (%s)…", email[:12])

    client.set_account_mode("PRACTICE")
    return client


async def _connect_client(client: Quotex) -> tuple[bool, str]:
    """
    Connect and, on success, save the refreshed ssid.
    Returns (success, reason).
    """
    global _last_connect_ok
    try:
        connected, reason = await client.connect()
    except Exception as e:
        return False, str(e)

    if connected:
        _last_connect_ok = time.time()
        # Persist new ssid so next restart is fast
        try:
            if client.api and hasattr(client.api, "ssid") and client.api.ssid:
                _save_ssid(client.api.ssid)
        except Exception:
            pass

    return connected, str(reason)


async def _get_client() -> Quotex:
    """Return connected Quotex client.  Strategy:
    1. Try existing client (check_connect).
    2. Try session-token reconnect.
    3. Fall back to email/password login (token expired).
    """
    global _client
    async with _connect_lock:
        # ── Fast path: existing client still alive ──────────────
        if _client is not None:
            try:
                alive = await asyncio.wait_for(_client.check_connect(), timeout=4.0)
                if alive:
                    return _client
            except Exception:
                pass
            # Connection dead — clean up
            try:
                await _client.close()
            except Exception:
                pass
            _client = None

        # ── Try session-token reconnect ─────────────────────────
        log.info("Quotex: reconnecting via session token…")
        _client = _make_client(use_email=False)
        connected, reason = await _connect_client(_client)
        if connected:
            log.info("Quotex: connected via session token (%s)", reason)
            return _client

        log.warning("Quotex: session token failed (%s) — trying email/password…", reason)

        # ── Fall back: full email/password login ────────────────
        if config.QUOTEX_EMAIL and config.QUOTEX_PASSWORD:
            try:
                await _client.close()
            except Exception:
                pass
            _client = _make_client(use_email=True)
            connected, reason = await _connect_client(_client)
            if connected:
                log.info("Quotex: connected via email/password (%s)", reason)
                return _client
            log.error("Quotex: email/password login also failed: %s", reason)
        else:
            log.error("Quotex: no email/password in config — cannot refresh session")

        _client = None
        raise ConnectionError(f"Quotex connect failed: {reason}")


async def _force_reconnect() -> Quotex:
    """Tear down current client and open a fresh connection."""
    global _client
    try:
        if _client is not None:
            await _client.close()
    except Exception:
        pass
    _client = None
    return await _get_client()


# ── Extra Quotex data accessors ───────────────────────────────

def get_traders_mood(pair: str) -> dict | None:
    global _client
    if _client is None or _client.api is None:
        return None
    qx_name = get_quotex_name(pair)
    return _client.api.traders_mood.get(qx_name)


def get_all_sizes(pair: str) -> dict | None:
    global _client
    if _client is None or _client.api is None:
        return None
    qx_name = get_quotex_name(pair)
    return _client.api.candle_generated_all_size_check.get(qx_name)


def get_instruments() -> list | dict | None:
    global _client
    if _client is None or _client.api is None:
        return None
    return _client.api.instruments


# ── Historical candle fetch ───────────────────────────────────

async def fetch_history(
    pair: str,
    granularity: int,
    count: int = 5000,
) -> list[dict[str, float]]:
    """
    Fetch up to `count` historical candles from Quotex via paginated WebSocket batches.
    Quotex caps each batch at ~49 candles, so we paginate backward in time.
    Returns list sorted oldest → newest.
    """
    qx_name = get_quotex_name(pair)
    client  = await _get_client()

    await client.start_candles_stream(qx_name, granularity)
    await asyncio.sleep(1.5)

    all_candles: dict[float, dict[str, float]] = {}
    fetch_time  = int(time.time())
    offset      = granularity * 200
    empty_runs  = 0

    log.info("Quotex history fetch: %s %ds — target %d candles", pair, granularity, count)

    while len(all_candles) < count:
        idx   = next(_req_counter)
        batch = await client._fetch_historical_batch(
            qx_name, fetch_time, offset, granularity, idx, timeout=15
        )

        if not batch:
            empty_runs += 1
            if empty_runs >= 3:
                log.warning("Quotex: 3 consecutive empty batches for %s %ds — stopping", pair, granularity)
                break
            await asyncio.sleep(1.0)
            continue

        data = batch.get("data", [])
        if not data:
            log.info("Quotex: no more data for %s %ds at fetch_time=%d", pair, granularity, fetch_time)
            break

        empty_runs = 0
        for c in data:
            ep = float(c["time"])
            all_candles[ep] = {
                "epoch": ep,
                "open":  float(c["open"]),
                "high":  float(c["high"]),
                "low":   float(c["low"]),
                "close": float(c["close"]),
            }

        earliest   = min(float(c["time"]) for c in data)
        fetch_time = int(earliest) - 1

        log.debug(
            "Quotex batch: %d candles this call, %d total, earliest=%d",
            len(data), len(all_candles), int(earliest),
        )
        await asyncio.sleep(0.05)

    result = sorted(all_candles.values(), key=lambda x: x["epoch"])
    log.info("Quotex history done: %s %ds — %d candles fetched", pair, granularity, len(result))
    return result[-count:] if len(result) > count else result


# ── Live WebSocket listener ───────────────────────────────────

TickCallback = Callable[[str, float, float], Awaitable[None]]

# How long a connection must stay healthy before retry_idx resets to 0
_HEALTHY_RESET_SEC = 120.0


async def start_listener(
    pairs: list[str],
    on_tick: TickCallback,
) -> None:
    """
    Live tick listener.

    Reconnect strategy:
      - Delay ladder: 2s → 5s → 10s → 30s → 60s (stays at 60s)
      - After 120s healthy → retry_idx resets (brief blips don't accumulate)
      - Session-token fast reconnect; auto-falls-back to email/password when token expires
      - Keepalive ping every 2s to detect silent drops
      - Per-pair stale detection: soft re-subscribe at 8s, hard reconnect at 22s
    """
    retry_idx   = 0
    last_price:  dict[str, float] = {}
    last_epoch:  dict[str, float] = {}
    price_idx:   dict[str, int]   = {}
    connected_at: float = 0.0

    async def _subscribe_all(client: Quotex) -> None:
        for pair in pairs:
            qx_name = get_quotex_name(pair)
            try:
                await client.start_candles_stream(qx_name, 60)
                await asyncio.sleep(0.1)
                try:
                    await client.api.subscribe_Traders_mood(qx_name, "turbo-option")
                    await asyncio.sleep(0.05)
                except Exception as e:
                    log.debug("Traders mood subscribe failed for %s: %s", pair, e)
                try:
                    await client.api.subscribe_all_size(qx_name)
                    await asyncio.sleep(0.05)
                except Exception as e:
                    log.debug("All-size subscribe failed for %s: %s", pair, e)
            except Exception as e:
                log.warning("Stream subscribe failed for %s: %s", pair, e)

        if client.api:
            for pair in pairs:
                qx_name = get_quotex_name(pair)
                pl = client.api.realtime_price.get(qx_name)
                price_idx[pair] = len(pl) if pl else 0
            known = list(client.api.realtime_price.keys())
            log.info("Quotex live: subscribed %d pairs — realtime_price keys: %s", len(pairs), known)

    while True:
        try:
            client = await _get_client()
            connected_at = time.time()
            log.info("Quotex live stream ready — polling %d pairs", len(pairs))
            retry_idx = 0   # fresh connection → reset backoff

            keepalive_stop = asyncio.Event()

            async def _keepalive() -> None:
                while not keepalive_stop.is_set():
                    await asyncio.sleep(_KEEPALIVE_SEC)
                    try:
                        alive = await asyncio.wait_for(client.check_connect(), timeout=3.0)
                        if not alive:
                            log.warning("Quotex keepalive: connection lost — reconnecting")
                            keepalive_stop.set()
                    except Exception as e:
                        log.warning("Quotex keepalive error: %s — reconnecting", e)
                        keepalive_stop.set()

            ka_task = asyncio.create_task(_keepalive())

            try:
                await _subscribe_all(client)

                poll_count  = 0
                resub_every = 60     # re-subscribe every 12s (60 × 0.2s)
                stale_every = 25     # stale-check every  5s (25 × 0.2s)
                STALE_SEC   = 8      # soft re-subscribe after 8s no ticks
                HARD_STALE  = 22     # hard reconnect after 22s no ticks
                last_tick_t = time.time()

                while not keepalive_stop.is_set():
                    if client.api is None:
                        await asyncio.sleep(1.0)
                        continue

                    poll_count += 1

                    # Reset retry backoff after sustained healthy period
                    if retry_idx > 0 and (time.time() - connected_at) > _HEALTHY_RESET_SEC:
                        retry_idx = 0
                        log.info("Quotex: connection healthy for %.0fs — retry backoff reset",
                                 time.time() - connected_at)

                    # Periodic re-subscribe (recovers from Phase-2 WS interference)
                    if poll_count % resub_every == 0:
                        await _subscribe_all(client)

                    ticks_fired = 0
                    for pair in pairs:
                        qx_name    = get_quotex_name(pair)
                        price_list = client.api.realtime_price.get(qx_name)
                        if not price_list:
                            continue

                        idx  = price_idx.get(pair, 0)
                        new  = price_list[idx:]
                        if not new:
                            continue
                        price_idx[pair] = len(price_list)

                        for entry in new:
                            price = float(entry.get("price") or 0)
                            epoch = float(entry.get("time")  or time.time())
                            if price > 0:
                                last_price[pair] = price
                                last_epoch[pair] = epoch
                                ticks_fired += 1
                                last_tick_t  = time.time()
                                await on_tick(pair, price, epoch)

                    # Stale-data check
                    if poll_count % stale_every == 0:
                        stale_sec = time.time() - last_tick_t
                        keys = list(client.api.realtime_price.keys()) if client.api else []

                        if stale_sec > HARD_STALE:
                            log.warning(
                                "Quotex: HARD STALE %.0fs — forcing full reconnect "
                                "(possible session expiry)", stale_sec
                            )
                            keepalive_stop.set()
                            break
                        elif stale_sec > STALE_SEC:
                            log.warning(
                                "Quotex: no ticks for %.0fs — re-subscribing. "
                                "keys: %s", stale_sec, keys
                            )
                            await _subscribe_all(client)
                        else:
                            log.info("Quotex heartbeat — last tick %.0fs ago", stale_sec)

                    await asyncio.sleep(0.2)

            finally:
                keepalive_stop.set()
                ka_task.cancel()
                try:
                    await ka_task
                except asyncio.CancelledError:
                    pass

        except ConnectionError as e:
            log.error("Quotex connection error: %s", e)
        except Exception as e:
            log.error("Quotex listener error: %s", e, exc_info=True)

        global _client
        try:
            if _client is not None:
                await _client.close()
        except Exception:
            pass
        _client = None

        delay = _RECONNECT_DELAYS[min(retry_idx, len(_RECONNECT_DELAYS) - 1)]
        retry_idx += 1
        log.info("Quotex: reconnecting in %.1f s (attempt %d)…", delay, retry_idx)
        await asyncio.sleep(delay)
