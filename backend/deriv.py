"""Deriv WebSocket connector — drop-in replacement for quotex.py."""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

import websockets

import config

log = logging.getLogger(__name__)

# ── Pair name mapping (internal → Deriv symbol) ──────────────

_PAIR_MAP: dict[str, str] = {
    "EURUSD": "frxEURUSD",
    "GBPUSD": "frxGBPUSD",
    "USDJPY": "frxUSDJPY",
    "AUDUSD": "frxAUDUSD",
    "USDCAD": "frxUSDCAD",
    "NZDUSD": "frxNZDUSD",
    "USDCHF": "frxUSDCHF",
    "XAUUSD": "frxXAUUSD",
}
_REV_MAP: dict[str, str] = {v: k for k, v in _PAIR_MAP.items()}

def _deriv_symbol(pair: str) -> str:
    return _PAIR_MAP.get(pair, f"frx{pair}")


def _is_forex_open() -> bool:
    """True when Forex market is live (Sun 21:00 – Fri 21:00 UTC)."""
    dt  = datetime.now(timezone.utc)
    dow = dt.weekday()  # 0=Mon … 6=Sun
    h   = dt.hour
    if dow == 4 and h >= 21:
        return False
    if dow == 5:
        return False
    if dow == 6 and h < 21:
        return False
    return True


# ── Stubs — Deriv doesn't expose traders-mood or order sizes ─

def get_traders_mood(pair: str) -> dict | None:
    return None


def get_all_sizes(pair: str) -> dict | None:
    return None


def get_instruments() -> list | dict | None:
    return None


# ── WebSocket URL ─────────────────────────────────────────────

def _ws_url() -> str:
    return f"wss://ws.derivws.com/websockets/v3?app_id={config.DERIV_APP_ID}"


# ── Auth helper ───────────────────────────────────────────────

async def _authorize(ws: Any) -> bool:
    await ws.send(json.dumps({"authorize": config.DERIV_TOKEN, "req_id": 1}))
    try:
        raw  = await asyncio.wait_for(ws.recv(), timeout=12.0)
        resp = json.loads(raw)
    except Exception as e:
        log.error("Deriv auth recv error: %s", e)
        return False
    if resp.get("error"):
        log.error("Deriv auth failed: %s", resp["error"].get("message", resp["error"]))
        return False
    if "authorize" not in resp:
        log.error("Deriv auth: unexpected response %s", list(resp.keys()))
        return False
    log.info("Deriv authorized — account: %s", resp["authorize"].get("loginid", "?"))
    return True


# ── Historical candle fetch ───────────────────────────────────

async def fetch_history(
    pair: str,
    granularity: int,
    count: int = 5000,
) -> list[dict[str, float]]:
    """
    Fetch up to `count` candles from Deriv via ticks_history.
    Paginates backward in batches of 1000 to stay well under API limits.
    Returns list sorted oldest → newest.
    """
    symbol     = _deriv_symbol(pair)
    all_candles: dict[float, dict[str, float]] = {}
    end_time   = "latest"
    BATCH      = 1000
    empty_runs = 0

    log.info("Deriv history: %s %ds → %s, target %d candles", pair, granularity, symbol, count)

    while len(all_candles) < count:
        need = min(count - len(all_candles), BATCH)

        req: dict[str, Any] = {
            "ticks_history":    symbol,
            "adjust_start_time": 1,
            "count":            need,
            "end":              end_time,
            "granularity":      granularity,
            "style":            "candles",
        }

        try:
            async with websockets.connect(
                _ws_url(),
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            ) as ws:
                if not await _authorize(ws):
                    break

                await ws.send(json.dumps(req))
                raw  = await asyncio.wait_for(ws.recv(), timeout=30.0)
                resp = json.loads(raw)

        except asyncio.TimeoutError:
            log.warning("Deriv history timeout for %s %ds", pair, granularity)
            empty_runs += 1
            if empty_runs >= 3:
                break
            await asyncio.sleep(2.0)
            continue
        except Exception as e:
            log.error("Deriv history WS error for %s: %s", pair, e)
            empty_runs += 1
            if empty_runs >= 3:
                break
            await asyncio.sleep(2.0)
            continue

        if resp.get("error"):
            msg = resp["error"].get("message", str(resp["error"]))
            log.error("Deriv history API error for %s: %s", pair, msg)
            break

        candles = resp.get("candles", [])
        if not candles:
            log.info("Deriv history: no more candles for %s %ds at end=%s", pair, granularity, end_time)
            break

        empty_runs = 0
        for c in candles:
            ep = float(c["epoch"])
            all_candles[ep] = {
                "epoch": ep,
                "open":  float(c["open"]),
                "high":  float(c["high"]),
                "low":   float(c["low"]),
                "close": float(c["close"]),
            }

        earliest = min(float(c["epoch"]) for c in candles)
        log.debug(
            "Deriv batch: %d candles, total=%d, earliest=%d",
            len(candles), len(all_candles), int(earliest),
        )

        if len(candles) < need:
            break   # broker has no more history

        end_time = int(earliest) - 1
        await asyncio.sleep(0.2)   # be polite between paginated requests

    result = sorted(all_candles.values(), key=lambda x: x["epoch"])
    log.info("Deriv history done: %s %ds — %d candles", pair, granularity, len(result))
    return result[-count:] if len(result) > count else result


# ── Live tick listener ────────────────────────────────────────

TickCallback = Callable[[str, float, float], Awaitable[None]]

_RECONNECT_DELAYS = [2.0, 5.0, 10.0, 30.0, 60.0]
_HEALTHY_RESET_SEC = 120.0


async def start_listener(
    pairs: list[str],
    on_tick: TickCallback,
) -> None:
    """
    Live tick stream via Deriv WebSocket subscriptions.

    Reconnect strategy:
      - Delay ladder: 2s → 5s → 10s → 30s → 60s
      - After 120 s healthy → retry_idx resets
      - Hard-reconnect if no ticks arrive within 22 s
    """
    retry_idx    = 0
    sym_to_pair  = {_deriv_symbol(p): p for p in pairs}

    while True:
        try:
            async with websockets.connect(
                _ws_url(),
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            ) as ws:
                if not await _authorize(ws):
                    raise ConnectionError("Deriv authorization failed")

                # Subscribe to all pairs
                for pair in pairs:
                    symbol = _deriv_symbol(pair)
                    await ws.send(json.dumps({"ticks": symbol, "subscribe": 1}))
                    await asyncio.sleep(0.1)

                log.info("Deriv live stream ready — %d pairs subscribed", len(pairs))
                connected_at = time.time()
                last_tick_t  = time.time()
                retry_idx    = 0

                async for raw in ws:
                    data = json.loads(raw)

                    if data.get("error"):
                        log.warning("Deriv WS error msg: %s",
                                    data["error"].get("message", data["error"]))
                        continue

                    # Reset backoff after sustained healthy period
                    if retry_idx > 0 and (time.time() - connected_at) > _HEALTHY_RESET_SEC:
                        retry_idx = 0

                    tick = data.get("tick")
                    if not tick:
                        continue

                    symbol = tick.get("symbol", "")
                    pair   = sym_to_pair.get(symbol)
                    if not pair:
                        continue

                    price = float(tick.get("quote", 0))
                    epoch = float(tick.get("epoch", time.time()))

                    if price <= 0:
                        continue

                    last_tick_t = time.time()
                    await on_tick(pair, price, epoch)

                    # Stale detection — if we somehow exit the async-for without
                    # disconnect, check inside the loop too
                    if (time.time() - last_tick_t) > 22.0:
                        log.warning("Deriv: no ticks for 22 s — reconnecting")
                        break

        except ConnectionError as e:
            log.error("Deriv connection error: %s", e)
        except websockets.exceptions.ConnectionClosed as e:
            log.warning("Deriv WS closed: %s", e)
        except Exception as e:
            log.error("Deriv listener error: %s", e, exc_info=True)

        delay = _RECONNECT_DELAYS[min(retry_idx, len(_RECONNECT_DELAYS) - 1)]
        retry_idx += 1
        log.info("Deriv: reconnecting in %.1f s (attempt %d)…", delay, retry_idx)
        await asyncio.sleep(delay)
