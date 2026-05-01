"""PLAYBIT AI — FastAPI backend with WebSocket signal delivery."""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import redis.asyncio as aioredis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import config
from advanced_features import compute_all_advanced
from candle_reaction import compute_candle_reaction
from deep_predictor import DeepPredictor
from features import compute_features, get_last_features
from harmonic_patterns import detect_harmonics, harmonic_confidence_boost
from institution import compute_institutional
from kalman_filter import KalmanPriceFilter
from meta_labeling import MetaLabeler
from mtf_analyzer import compute_mtf_agreement, mtf_score_pts
from news import fetch_news_events, is_news_window
from ppo_agent import PPOAgent
from predictor import TabularPredictor
from regime import RegimeDetector
from rule_engine import rule_engine_predict
from signal_scorer import (
    ai_score_pts, apply_consensus_penalty, consensus_check,
    grade, reaction_score_pts, score_to_confidence, smc_score_pts,
)
from smc import compute_smc_features
from stacking_model import StackingEnsemble
from telegram_bot import send_signal_alert
from timing_engine import aggregate_samples
from window_selector import WindowSelector

import deriv as deriv_client
from tick_store import tick_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("playbit")

app = FastAPI(title="PLAYBIT AI", version="6.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── State ────────────────────────────────────────────────────

redis_client: aioredis.Redis | None = None

# candle_store[symbol][granularity] = list of candle dicts
candle_store: dict[str, dict[int, list[dict[str, Any]]]] = {}

# per-pair ML models
tabular_models:  dict[str, TabularPredictor] = {}
deep_models:     dict[str, DeepPredictor]    = {}
stacking_models: dict[str, StackingEnsemble] = {}
ppo_agents:      dict[str, PPOAgent]         = {}
meta_labelers:   dict[str, MetaLabeler]      = {}
kalman_filters:  dict[str, KalmanPriceFilter]= {}
regime_detectors:dict[str, RegimeDetector]   = {}
window_selectors:dict[str, WindowSelector]   = {}

# circuit breaker: consecutive losses per pair
loss_streak:     dict[str, int]              = {}

# last broadcast signal per pair — surfaced via /api/signal/{pair}
last_signal_per_pair: dict[str, dict[str, Any]] = {}

# tick builders: pair → current 1M candle being assembled from raw ticks
_tick_builders:  dict[str, dict]             = {}

# throttle live tick broadcasts — 250 ms balances smoothness vs WS overhead
_last_live_broadcast: dict[str, float]       = {}
_LIVE_BROADCAST_INTERVAL = 0.25

# active WebSocket clients
ws_clients: set[WebSocket] = set()


# ── Lifecycle ────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    global redis_client
    try:
        redis_client = aioredis.from_url(config.REDIS_URL, decode_responses=True)
        await redis_client.ping()
        log.info("Redis connected: %s", config.REDIS_URL)
    except Exception as e:
        log.warning("Redis unavailable (%s) — running without streamer", e)
        redis_client = None

    # Initialize per-pair state
    for pair in config.ALL_PAIRS:
        candle_store[pair]     = {}
        kalman_filters[pair]   = KalmanPriceFilter()
        window_selectors[pair] = WindowSelector()
        regime_detectors[pair] = RegimeDetector()
        meta_labelers[pair]    = MetaLabeler()
        ppo_agents[pair]       = PPOAgent()
        stacking_models[pair]  = StackingEnsemble()
        loss_streak[pair]      = 0

    # Start background tasks (no crypto listener — forex only)
    asyncio.create_task(redis_subscriber())
    asyncio.create_task(news_refresher())
    asyncio.create_task(initial_history_loader())


# ── History Loading ──────────────────────────────────────────

_history_sem = asyncio.Semaphore(4)  # max 4 concurrent Deriv WS connections
_train_sem   = asyncio.Semaphore(2)  # max 2 concurrent ML training jobs


async def initial_history_loader() -> None:
    """Load historical candles for all pairs on startup."""
    log.info("Loading historical candles…")
    tasks: list[asyncio.Task[None]] = []
    for pair in config.ALL_PAIRS:
        tasks.append(asyncio.create_task(load_history_for_pair(pair)))
    await asyncio.gather(*tasks, return_exceptions=True)
    log.info("Historical load complete.")


async def load_history_for_pair(pair: str) -> None:
    for granularity, target in config.CHART_CANDLE_COUNT.items():
        try:
            async with _history_sem:
                # Use paginated fetch to collect full chart history
                candles = await deriv_client.fetch_history_paginated(
                    pair, granularity, total_count=target
                )

            if candles:
                candle_store[pair][granularity] = candles
                log.info(
                    "Loaded %d × %ds candles for %s (target %d)",
                    len(candles), granularity, pair, target,
                )

                # Push 1M history to already-connected clients
                if granularity == 60:
                    await broadcast({
                        "type":        "history",
                        "pair":        pair,
                        "granularity": 60,
                        "candles":     candles,
                    })
                    if len(candles) >= config.MIN_CANDLES:
                        asyncio.create_task(train_models_for_pair(pair))
        except Exception as e:
            log.error("History load error %s %ds: %s", pair, granularity, e)


async def train_models_for_pair(pair: str) -> None:
    candles = candle_store[pair].get(60, [])
    if len(candles) < config.MIN_CANDLES:
        return

    # Tell clients training has started so the frontend stops showing "0 candles"
    await broadcast({
        "type":       "model_status",
        "pair":       pair,
        "is_trained": False,
        "accuracy":   0.0,
        "n_candles":  len(candles),
        "training":   True,
    })

    async with _train_sem:
        df = _candles_to_df(candles)
        now_hour = datetime.now(timezone.utc).hour

        tab = tabular_models.get(pair)
        if tab is None:
            tab = TabularPredictor(pair, 60)
            tabular_models[pair] = tab

        # Run sync training in thread pool to keep event loop responsive
        await asyncio.to_thread(tab.train, df, now_hour)

        dp = deep_models.get(pair)
        if dp is None:
            dp = DeepPredictor(pair)
            deep_models[pair] = dp
        if len(candles) >= 100:
            await asyncio.to_thread(dp.train, df, now_hour)

    await broadcast({
        "type":       "model_retrained",
        "pair":       pair,
        "is_trained": bool(tab.trained),
        "accuracy":   tab.accuracy,
        "n_candles":  tab.n_candles,
        "training":   False,
    })


# ── Redis Subscriber ─────────────────────────────────────────

async def redis_subscriber() -> None:
    asyncio.create_task(direct_deriv_listener())

    if redis_client is None:
        log.info("Redis not available — direct Deriv listener only")
        return

    log.info("Subscribing to Redis candle channels…")
    pubsub = redis_client.pubsub()
    patterns = [f"candle:{pair}:*" for pair in config.ALL_PAIRS]
    for p in patterns:
        await pubsub.psubscribe(p)

    async for msg in pubsub.listen():
        if msg["type"] != "pmessage":
            continue
        try:
            channel = str(msg["channel"])
            parts   = channel.split(":")
            if len(parts) < 3:
                continue
            pair_name = parts[1]
            gran      = int(parts[2])
            data      = json.loads(str(msg["data"]))
            await on_candle(pair_name, gran, data)
        except Exception as e:
            log.error("Redis msg error: %s", e)


async def direct_deriv_listener() -> None:
    """
    Lossless tick capture from Deriv WebSocket for all forex pairs.

    - Subscribes via `ticks` stream (server-pushed, no polling).
    - Auto-reconnect with exponential backoff (1s → 60s).
    - Resubscribes all pairs on reconnect — no ticks dropped after recovery.
    - Built-in WebSocket ping/pong keeps connection alive across NAT/idle.
    """
    import websockets as ws_lib

    url        = f"wss://ws.binaryws.com/websockets/v3?app_id={config.DERIVE_APP_ID}"
    backoff    = 1.0
    backoff_max = 60.0

    while True:
        try:
            async with ws_lib.connect(
                url, ping_interval=15, ping_timeout=30, close_timeout=5,
                max_size=2 ** 20,
            ) as ws:
                # Authorise
                await ws.send(json.dumps({"authorize": config.DERIVE_TOKEN}))
                auth = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if auth.get("error"):
                    log.error("Deriv auth failed: %s — backing off", auth["error"])
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, backoff_max)
                    continue

                # Subscribe to all pairs (forex only)
                deriv_subs = list(config.FOREX_PAIRS)
                log.info("Deriv tick listener up — subscribing %d symbols", len(deriv_subs))
                for pair in deriv_subs:
                    await ws.send(json.dumps({"ticks": pair, "subscribe": 1}))
                    await asyncio.sleep(0.02)  # rate-limit safe (Deriv allows ~50/s)

                backoff = 1.0  # successful connection — reset

                # Receive loop. Long timeout because forex is silent on weekends;
                # synthetic indices (R_*) tick 24/7, so any silence > 120s = real drop.
                while True:
                    raw  = await asyncio.wait_for(ws.recv(), timeout=120)
                    data = json.loads(raw)
                    if "tick" in data:
                        tick     = data["tick"]
                        pair_sym = str(tick.get("symbol", ""))
                        price    = float(tick.get("quote", 0))
                        epoch    = float(tick.get("epoch", time.time()))
                        if price > 0 and pair_sym:
                            await _process_tick(pair_sym, price, epoch)
                    elif data.get("error"):
                        log.warning("Deriv error msg: %s", data["error"].get("message"))

        except asyncio.TimeoutError:
            log.warning("Deriv tick stream silent > 120s — reconnecting")
        except Exception as e:
            log.error("Deriv listener error: %s — reconnecting in %.1fs", e, backoff)

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, backoff_max)




async def _process_tick(pair: str, price: float, epoch: float) -> None:
    """Persist every tick → build 1M OHLC → emit closed + live candle updates."""

    # 1. Persist raw tick (every single one — never dropped)
    tick_store.append(pair, epoch, price)

    # 2. Aggregate into 1M OHLC builder
    minute_epoch = int(epoch) // 60 * 60  # floor to minute boundary
    builder      = _tick_builders.get(pair)

    if builder is None or builder["minute_epoch"] != minute_epoch:
        # New minute — finalise the previous candle as closed
        if builder and builder["count"] > 0:
            await on_candle(pair, 60, {
                "epoch":  float(builder["minute_epoch"]),
                "open":   builder["open"],
                "high":   builder["high"],
                "low":    builder["low"],
                "close":  builder["close"],
                "closed": True,
            })
        # Start fresh builder for this minute
        _tick_builders[pair] = {
            "minute_epoch": minute_epoch,
            "open":  price,
            "high":  price,
            "low":   price,
            "close": price,
            "count": 1,
        }
    else:
        builder["high"]  = max(builder["high"], price)
        builder["low"]   = min(builder["low"],  price)
        builder["close"] = price
        builder["count"] += 1

    # 3. Emit live (not-yet-closed) candle update so chart stays in sync
    b = _tick_builders[pair]
    await on_candle(pair, 60, {
        "epoch":  float(b["minute_epoch"]),
        "open":   b["open"],
        "high":   b["high"],
        "low":    b["low"],
        "close":  price,
        "closed": False,
    })


# ── Candle Processing ────────────────────────────────────────

async def on_candle(pair: str, granularity: int, candle: dict[str, Any]) -> None:
    if pair not in candle_store:
        candle_store[pair] = {}

    store = candle_store[pair].setdefault(granularity, [])

    # NOTE: do NOT mutate candle.open to match previous close — that would
    # distort real market data. Real gaps (weekend, news) must be preserved.

    # Merge with existing open candle or append
    if store and store[-1]["epoch"] == candle["epoch"]:
        store[-1] = candle
    else:
        store.append(candle)

    # Trim history
    if len(store) > config.HISTORY_COUNT:
        candle_store[pair][granularity] = store[-config.HISTORY_COUNT:]

    # Kalman filter update on 1M close price
    if granularity == 60:
        kf = kalman_filters.get(pair)
        if kf:
            kf.update(float(candle["close"]))

    # Aggregate 1M into higher TFs so 2M/5M/15M/1h/4h chart stays live
    if granularity == 60:
        await _aggregate_higher_tfs(pair, candle)

    # Broadcast: always for closed candles; throttle live updates per pair
    is_closed = bool(candle.get("closed", False))
    now_t     = time.time()
    last_t    = _last_live_broadcast.get(pair, 0.0)
    if is_closed or (now_t - last_t) >= _LIVE_BROADCAST_INTERVAL:
        await broadcast({
            "type":        "candle_closed" if is_closed else "candle_update",
            "pair":        pair,
            "granularity": granularity,
            "candle":      candle,
        })
        if not is_closed:
            _last_live_broadcast[pair] = now_t

    # Check if 1M candle closed → run signal pipeline
    if granularity == 60 and is_closed:
        await on_1m_close(pair, candle)


# Higher timeframes (in seconds) we keep live by aggregating 1M candles
_HIGHER_TFS = (120, 300, 900, 3600, 14400)


async def _aggregate_higher_tfs(pair: str, m1_candle: dict[str, Any]) -> None:
    """Roll the latest 1M candle into all higher timeframe buckets."""
    epoch = float(m1_candle["epoch"])
    open_ = float(m1_candle["open"])
    high  = float(m1_candle["high"])
    low   = float(m1_candle["low"])
    close = float(m1_candle["close"])

    pair_store = candle_store.setdefault(pair, {})

    for tf in _HIGHER_TFS:
        bucket_epoch = int(epoch) // tf * tf
        store = pair_store.setdefault(tf, [])

        if store and store[-1]["epoch"] == bucket_epoch:
            # Update existing bucket
            agg = store[-1]
            agg["high"]  = max(float(agg["high"]), high)
            agg["low"]   = min(float(agg["low"]),  low)
            agg["close"] = close
        else:
            # Start a new bucket — mark previous as closed
            if store:
                store[-1]["closed"] = True
            store.append({
                "epoch":  float(bucket_epoch),
                "open":   open_,
                "high":   high,
                "low":    low,
                "close":  close,
                "closed": False,
            })

        if len(store) > config.HISTORY_COUNT:
            pair_store[tf] = store[-config.HISTORY_COUNT:]

        # Throttle live broadcast same as 1M
        now_t  = time.time()
        last_t = _last_live_broadcast.get(f"{pair}:{tf}", 0.0)
        if (now_t - last_t) >= _LIVE_BROADCAST_INTERVAL:
            await broadcast({
                "type":        "candle_update",
                "pair":        pair,
                "granularity": tf,
                "candle":      store[-1],
            })
            _last_live_broadcast[f"{pair}:{tf}"] = now_t


async def on_1m_close(pair: str, closed_candle: dict[str, Any]) -> None:
    """Full signal pipeline triggered on every 1M candle close."""
    log.debug("1M close: %s @ %s", pair, closed_candle.get("epoch"))

    candles_1m = candle_store[pair].get(60, [])

    # Guard: need minimum candles before pipeline runs
    if len(candles_1m) < config.MIN_CANDLES:
        await broadcast({
            "type":   "signal_blocked",
            "pair":   pair,
            "reason": "loading_history",
        })
        return

    # Use only recent candles for analysis (fast computation, focused context)
    # Training uses all candles; pipeline only needs the last N
    analysis_n = config.ANALYSIS_CANDLE_COUNT.get(60, 500)
    df_1m      = _candles_to_df(candles_1m[-analysis_n:])

    if len(df_1m) < config.MIN_CANDLES:
        # Tell the frontend why nothing is happening — otherwise the panel
        # gets stuck on whatever was last broadcast.
        await broadcast({
            "type":   "signal_blocked",
            "pair":   pair,
            "reason": "loading_history",
        })
        return

    # Signal gate checks (hard blocks only)
    gate = _check_signal_gates(pair, df_1m)
    if gate:
        await broadcast({
            "type":   "signal_blocked",
            "pair":   pair,
            "reason": gate,
        })
        return

    # Build MTF candle dict — use analysis slice per TF
    candle_dfs: dict[int, pd.DataFrame] = {}
    for gran in config.MTF_ANALYSIS_TFS:
        c = candle_store[pair].get(gran, [])
        if c:
            n = config.ANALYSIS_CANDLE_COUNT.get(gran, 200)
            candle_dfs[gran] = _candles_to_df(c[-n:])

    utc_hour   = datetime.now(timezone.utc).hour
    utc_minute = datetime.now(timezone.utc).minute

    # Run full analysis — strict pipeline per Master Plan §18
    signal = await _run_signal_pipeline(pair, df_1m, candle_dfs, utc_hour, utc_minute)

    # Plan §8: SKIP < 0.65 → no signal emitted
    if signal["grade"] == "SKIP":
        await broadcast({
            "type":   "signal_blocked",
            "pair":   pair,
            "reason": signal.get("reason", "below_threshold"),
        })
        return

    epoch  = int(closed_candle.get("epoch", time.time()))
    ws_sel = window_selectors.get(pair)
    mtf    = signal.get("mtf_context", {})
    mtf_ag = float(mtf.get("agreement", 0.5)) if isinstance(mtf, dict) else 0.5
    react  = signal.get("candle_reaction", {})
    net    = float(react.get("net_score", 0)) if isinstance(react, dict) else 0.0

    # Plan §17: 5M window holds at most 3 best signals; minute_idx ≥ 1 only.
    if ws_sel and not ws_sel.try_add(
        epoch, float(signal["confidence"]), str(signal["signal"]),
        str(signal["grade"]), mtf_ag, net,
    ):
        await broadcast({
            "type":   "signal_blocked",
            "pair":   pair,
            "reason": "window_full",
        })
        return

    signal["window_plan"]       = ws_sel.get_window_plan() if ws_sel else []
    signal["candle_open_time"]  = epoch
    signal["candle_close_time"] = epoch + 60
    signal["timestamp"]         = time.time()

    last_signal_per_pair[pair] = {**signal, "pair": pair}

    await broadcast({"type": "signal", "pair": pair, **signal})

    # Retrain if needed
    tab = tabular_models.get(pair)
    if tab and tab.should_retrain(1):
        asyncio.create_task(train_models_for_pair(pair))

    # Telegram alert — plan §21 filters to HIGH+ inside
    asyncio.create_task(send_signal_alert(signal, pair))


async def _run_signal_pipeline(
    pair: str,
    df_1m: pd.DataFrame,
    candle_dfs: dict[int, pd.DataFrame],
    utc_hour: int,
    utc_minute: int,
) -> dict[str, Any]:
    """Full 4-layer scoring pipeline."""

    # ── Feature computation ──────────────────────────────────
    feat = get_last_features(df_1m, utc_hour)
    adx  = float(feat.get("adx_14", 0))

    # ── SMC/ICT ──────────────────────────────────────────────
    smc  = compute_smc_features(df_1m, utc_hour)

    # ── Institutional ────────────────────────────────────────
    inst = compute_institutional(df_1m, utc_hour, utc_minute)

    # ── Advanced (Wyckoff, Elliott, S/D) ─────────────────────
    advanced = compute_all_advanced(df_1m)
    wyckoff  = {k: int(v) for k, v in advanced.items() if k.startswith("wyckoff")}
    elliott  = {k: float(v) for k, v in advanced.items() if k.startswith("elliott")}
    sd       = {k: float(v) for k, v in advanced.items() if k.startswith("sd_")}

    # ── Candle Reaction ──────────────────────────────────────
    reaction = compute_candle_reaction(df_1m, smc=smc)

    # ── Harmonic Patterns ────────────────────────────────────
    harmonics = detect_harmonics(df_1m)

    # ── MTF Analysis (advisory; soft penalty later) ─────────
    mtf = compute_mtf_agreement(candle_dfs, utc_hour)
    mtf_agreement = float(mtf.get("agreement", 0))
    mtf_dir_str   = str(mtf.get("direction", "neutral"))

    # ── AI Models ────────────────────────────────────────────
    tab = tabular_models.get(pair)
    tab_result = tab.predict(df_1m, utc_hour) if tab else {}
    tab_probs  = tab_result.get("model_probs", {}) if isinstance(tab_result, dict) else {}
    tab_dir    = str(tab_result.get("direction", "SKIP")) if isinstance(tab_result, dict) else "SKIP"

    dp = deep_models.get(pair)
    deep_probs = dp.predict(df_1m, utc_hour) if dp else {}
    bayes = dp.predict_bayesian(df_1m, utc_hour) if dp else {"bayes_mean": 0.5, "bayes_std": 0.1}

    rule_result = rule_engine_predict(df_1m, smc, inst, reaction, utc_hour)
    rule_dir    = str(rule_result.get("direction", "SKIP"))
    rule_conf   = float(rule_result.get("confidence", 0.5))

    all_model_probs: dict[str, float] = {}
    all_model_probs.update(tab_probs)  # type: ignore[arg-type]
    all_model_probs.update(deep_probs)
    # Bug 1.3 fix: convert RED confidence to GREEN probability space
    if rule_dir == "GREEN":
        all_model_probs["rule"] = rule_conf
    elif rule_dir == "RED":
        all_model_probs["rule"] = 1.0 - rule_conf
    else:
        all_model_probs["rule"] = 0.5
    all_model_probs["bayes"] = float(bayes.get("bayes_mean", 0.5))

    # Bug 4.10 fix: accuracy-weighted fusion instead of plain mean
    if all_model_probs:
        _weights: list[float] = []
        _probs:   list[float] = []
        for _name, _prob in all_model_probs.items():
            _acc = float(
                (tab.model_accuracies.get(_name) if tab else None)
                or (dp.accuracy_map.get(_name) if dp else None)
                or 0.52
            )
            _weights.append(max(0.02, _acc - 0.50))
            _probs.append(_prob)
        _total_w = sum(_weights) or 1.0
        base_fused = float(sum(p * w for p, w in zip(_probs, _weights)) / _total_w)
    else:
        base_fused = 0.5

    stack_model = stacking_models.get(pair)
    if stack_model and stack_model.trained:
        stack_prob = stack_model.predict(list(all_model_probs.values()))
        base_fused = stack_model.fuse(base_fused, stack_prob)

    # ── Direction (always resolved; never SKIP at this stage) ─
    # Fallback chain: AI fused → rule engine → last-bar close diff
    if tab_dir != "SKIP":
        direction = "GREEN" if base_fused > 0.5 else "RED"
    elif rule_dir != "SKIP":
        direction = rule_dir
    elif len(df_1m) >= 2:
        last_close = float(df_1m["close"].iloc[-1])
        prev_close = float(df_1m["close"].iloc[-2])
        direction  = "GREEN" if last_close >= prev_close else "RED"
    else:
        direction = "GREEN" if base_fused >= 0.5 else "RED"

    # ── Kalman ───────────────────────────────────────────────
    kf = kalman_filters.get(pair)
    kalman_trend_d = kf.get_trend() if kf else {}
    kt = int(kalman_trend_d.get("kalman_trend", 0))
    kv = float(kalman_trend_d.get("kalman_velocity", 0))
    kalman_agree = (kt == 1 and direction == "GREEN") or (kt == -1 and direction == "RED")

    # ── PPO advisory (soft penalty, no skip) ────────────────
    ppo = ppo_agents.get(pair)
    ppo_skip = False
    if ppo:
        ppo_skip = ppo.decide(feat, base_fused, direction) == "SKIP"

    # ── Meta-labeling (soft penalty when low) ───────────────
    meta = meta_labelers.get(pair)
    meta_prob = meta.predict(feat) if meta else 0.75

    # ── 4-Layer Scoring ──────────────────────────────────────
    mtf_pts    = mtf_score_pts(mtf)
    smc_pts    = smc_score_pts(smc, inst, wyckoff, harmonics, sd, elliott, direction=direction)
    react_pts  = reaction_score_pts(reaction)
    ai_pts_val = ai_score_pts(base_fused, meta_prob)

    confidence = score_to_confidence(
        mtf_pts, smc_pts, react_pts, ai_pts_val,
        meta_prob >= 0.60, adx, kalman_agree, kv,
    )

    # AI-aligned blend: a strongly-confident AI vote should not be killed by
    # weak SMC/reaction context. Take the better of layer-score or direction-
    # aligned AI confidence (with a small handicap so scoring still matters).
    ai_aligned = base_fused if direction == "GREEN" else (1.0 - base_fused)
    confidence = max(confidence, ai_aligned - 0.10)

    h_boost = harmonic_confidence_boost(harmonics, direction)
    confidence = min(confidence + h_boost, 0.99)

    # Direction-aligned patterns
    pattern_names: list[str] = []
    if direction == "GREEN":
        if reaction.get("pin_bull"):    pattern_names.append("pin_bar_bull")
        if reaction.get("bull_engulf"): pattern_names.append("bullish_engulfing")
    else:
        if reaction.get("pin_bear"):    pattern_names.append("pin_bar_bear")
        if reaction.get("bear_engulf"): pattern_names.append("bearish_engulfing")
    for h_name, h_val in harmonics.items():
        if not h_val:
            continue
        if direction == "GREEN" and h_name.endswith("_bull"):
            pattern_names.append(h_name)
        elif direction == "RED" and h_name.endswith("_bear"):
            pattern_names.append(h_name)
    pattern_present = len(pattern_names) > 0

    # 8-check consensus filter (penalty/boost; never blocks)
    cons_score = consensus_check(feat, direction, pattern_present)
    confidence = apply_consensus_penalty(confidence, cons_score)

    # ── Soft penalties for conflicting context ──────────────
    # MTF direction opposes signal direction — strongest penalty
    if mtf_dir_str != "neutral":
        if (direction == "GREEN" and mtf_dir_str == "bear") or \
           (direction == "RED"   and mtf_dir_str == "bull"):
            confidence *= 0.85
    if mtf_agreement < config.MTF_MIN_AGREEMENT:
        confidence *= 0.92
    if ppo_skip:
        confidence *= 0.85
    if meta_prob < 0.60:
        confidence *= 0.88
    if cons_score < config.SIGNAL_MIN_EMIT_CONFIRM:
        confidence *= 0.92
    if adx < 15:
        confidence *= 0.85  # choppy market

    # ATR-spike (volatility): current candle range > 3× rolling ATR
    if len(df_1m) >= 21:
        try:
            atr_series_p   = (df_1m["high"] - df_1m["low"]).rolling(14).mean()
            current_range  = float(df_1m["high"].iloc[-1] - df_1m["low"].iloc[-1])
            prev_avg_range = float(atr_series_p.iloc[-2])
            if prev_avg_range > 0 and current_range > 3 * prev_avg_range:
                confidence *= 0.85
        except Exception:
            pass

    confidence = max(0.0, min(confidence, 0.99))

    grade_str = grade(confidence)
    if grade_str == "SKIP":
        return _skip_signal("low_score")

    # Plan §18: 5 consecutive losses → 30 min pause (kept as hard block)
    if loss_streak.get(pair, 0) >= 5:
        return _skip_signal("circuit_break")

    return {
        "signal":           direction,
        "confidence":       round(confidence, 4),
        "grade":            grade_str,
        "trade_type":       config.SIGNAL_EXPIRY_TYPE,
        "expiry_bars":      config.SIGNAL_EXPIRY_BARS,
        "max_delay_sec":    config.SIGNAL_MAX_DELAY_SEC,
        "high_confidence":  confidence >= config.GRADE_ELITE,
        "hard_confirmed":   cons_score >= config.SIGNAL_CONFIRM_REQUIRED,
        "mtf_context":      mtf,
        "smc_context":      smc,
        "candle_reaction":  reaction,
        "patterns":         pattern_names,
        "ai_models":        all_model_probs,
        "bayes_uncertainty":bayes,
        "kalman":           {"trend": kt, "velocity": kv, "agree": kalman_agree},
        "consensus_score":  cons_score,
        "layers": {
            "mtf_pts":   mtf_pts,
            "smc_pts":   smc_pts,
            "react_pts": react_pts,
            "ai_pts":    ai_pts_val,
        },
    }


def _check_signal_gates(pair: str, df: pd.DataFrame) -> str:
    """Returns non-empty string (reason) if signal should be blocked."""
    now = time.time()

    # Market closed (Forex: Fri 21:00 – Sun 21:00 UTC)
    if True:  # all pairs are forex
        dt  = datetime.now(timezone.utc)
        dow = dt.weekday()  # 0=Mon, 5=Sat, 6=Sun
        h   = dt.hour
        if dow == 4 and h >= 21:
            return "market_closed"
        if dow in (5, 6):
            return "market_closed"

    # News window ±15 min — kept as a hard block (high-impact whipsaw risk).
    if is_news_window(pair, now):
        return "news_window"

    # Note: choppy_market (ADX < 15) and ATR-spike are now SOFT penalties
    # applied inside _run_signal_pipeline — they reduce confidence rather
    # than block emission outright. This gives the user more frequent signals
    # while still letting the grade system filter low-quality setups.

    return ""


def _skip_signal(reason: str) -> dict[str, Any]:
    return {"signal": "SKIP", "grade": "SKIP", "confidence": 0.0, "reason": reason}


# ── WebSocket ────────────────────────────────────────────────

@app.websocket("/ws/{pair}")
async def websocket_endpoint(ws: WebSocket, pair: str) -> None:
    await ws.accept()
    ws_clients.add(ws)
    log.info("WS connected: %s — %s", pair, ws.client)

    try:
        # Send market status
        await ws.send_json({"type": "market_status", "open": True, "pair": pair})

        # Send history
        candles = candle_store.get(pair, {}).get(60, [])
        if candles:
            await ws.send_json({
                "type":        "history",
                "pair":        pair,
                "granularity": 60,
                "candles":     candles,
            })

        # Send model status — fall back to candle-store count when no
        # TabularPredictor exists yet (e.g. fresh pair still buffering history)
        tab = tabular_models.get(pair)
        loaded_count = len(candle_store.get(pair, {}).get(60, []))
        n_for_status = tab.n_candles if (tab and tab.trained) else loaded_count
        await ws.send_json({
            "type":       "model_status",
            "pair":       pair,
            "is_trained": bool(tab and tab.trained),
            "accuracy":   tab.accuracy if tab else 0.0,
            "n_candles":  n_for_status,
        })

        while True:
            await ws.receive_text()  # keep alive

    except WebSocketDisconnect:
        log.info("WS disconnected: %s", pair)
    finally:
        ws_clients.discard(ws)


async def broadcast(msg: dict[str, Any]) -> None:
    dead: set[WebSocket] = set()
    for ws in ws_clients:
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    ws_clients.difference_update(dead)


# ── Background Tasks ─────────────────────────────────────────

async def news_refresher() -> None:
    while True:
        try:
            await fetch_news_events()
        except Exception:
            pass
        await asyncio.sleep(3600)


# ── REST Endpoints ───────────────────────────────────────────

@app.get("/api/market-hours")
async def get_market_hours() -> dict[str, Any]:
    """Return forex market open/closed status and weekly schedule (UTC)."""
    now = datetime.now(timezone.utc)
    dow = now.weekday()   # 0=Mon … 6=Sun
    h   = now.hour
    m   = now.minute

    closed = (dow == 4 and (h > 21 or (h == 21 and m >= 0))) or dow in (5, 6) or \
             (dow == 6 and h < 21)

    return {
        "is_open": not closed,
        "current_utc": now.strftime("%Y-%m-%d %H:%M UTC"),
        "schedule": {
            "open":  "Sunday 21:00 UTC",
            "close": "Friday 21:00 UTC",
        },
        "closed_days": [
            "Friday 21:00 UTC → Saturday (all day)",
            "Sunday (all day until 21:00 UTC)",
        ],
        "note": "Forex trades Sunday 21:00 – Friday 21:00 UTC",
    }


@app.get("/api/pairs")
async def get_pairs() -> dict[str, Any]:
    return {"forex": config.FOREX_PAIRS, "status": "ok"}


@app.get("/api/candles/{pair}")
async def get_candles(pair: str, granularity: int = 60, count: int = 5000) -> dict[str, Any]:
    candles = candle_store.get(pair, {}).get(granularity, [])
    return {"pair": pair, "granularity": granularity, "candles": candles[-count:] if count > 0 else candles}


@app.get("/api/signal/{pair}")
async def get_last_signal(pair: str) -> dict[str, Any]:
    sig = last_signal_per_pair.get(pair)
    return {"pair": pair, "signal": sig}


@app.get("/api/ticks/{pair}")
async def get_ticks(pair: str, count: int = 200, since: float | None = None) -> dict[str, Any]:
    """Raw tick history for a pair. Use ?since=epoch for incremental fetch."""
    buf = tick_store.get(pair)
    if since is not None:
        ticks = buf.since(since)
    else:
        ticks = buf.latest(count)
    return {
        "pair":     pair,
        "count":    len(ticks),
        "received": buf.total_received,
        "buffered": buf.buffered,
        "ticks":    [{"epoch": e, "price": p} for (e, p) in ticks],
    }


@app.get("/api/ticks-stats")
async def get_tick_stats() -> dict[str, Any]:
    """Per-pair tick capture stats — useful for verifying lossless ingestion."""
    return {
        "uptime_sec": time.time() - tick_store.started_at,
        "symbols":    tick_store.stats(),
    }


@app.get("/api/model/{pair}")
async def get_model_status(pair: str) -> dict[str, Any]:
    tab  = tabular_models.get(pair)
    deep = deep_models.get(pair)
    return {
        "pair":         pair,
        "tabular":      {"trained": bool(tab and tab.trained), "accuracy": tab.accuracy if tab else 0, "n_candles": tab.n_candles if tab else 0},
        "deep":         {"trained": bool(deep and deep.trained), "models": list(deep.accuracy_map.keys()) if deep else []},
    }


@app.post("/api/result/{pair}")
async def report_result(pair: str, won: bool) -> dict[str, Any]:
    """Report trade outcome — updates circuit breaker."""
    if won:
        loss_streak[pair] = 0
    else:
        loss_streak[pair] = loss_streak.get(pair, 0) + 1
    return {"pair": pair, "streak": loss_streak.get(pair, 0)}


# ── Helpers ──────────────────────────────────────────────────

def _candles_to_df(candles: list[dict[str, Any]]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=["epoch","open","high","low","close"])
    df = pd.DataFrame(candles)
    for col in ["open", "high", "low", "close"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("epoch").reset_index(drop=True)
    return df


# ── Frontend (bundled React build) ───────────────────────────

import os as _os
_static = _os.path.join(_os.path.dirname(__file__), "static")
if _os.path.isdir(_static):
    app.mount("/assets", StaticFiles(directory=f"{_static}/assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa(full_path: str) -> FileResponse:
        return FileResponse(f"{_static}/index.html")


# ── Entry point ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=config.API_HOST, port=config.API_PORT, reload=False)
