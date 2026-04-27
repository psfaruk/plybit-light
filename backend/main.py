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
import binance as binance_client

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

# tick builders: pair → current 1M candle being assembled from raw ticks
_tick_builders:  dict[str, dict]             = {}

# throttle live tick broadcasts (closed candles always pass through)
_last_live_broadcast: dict[str, float]       = {}
_LIVE_BROADCAST_INTERVAL = 0.5  # seconds between live tick broadcasts per pair

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

    # Start background tasks
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
    is_crypto = pair in config.CRYPTO_PAIRS

    for granularity in list(config.CANDLE_COUNT.keys()):
        try:
            async with _history_sem:
                if is_crypto:
                    candles = await binance_client.fetch_history(pair, granularity)
                else:
                    candles = await deriv_client.fetch_history(pair, granularity)

            if candles:
                candle_store[pair][granularity] = candles
                log.info("Loaded %d × %ds candles for %s", len(candles), granularity, pair)

                # Push 1M history to already-connected clients immediately
                if granularity == 60:
                    await broadcast({
                        "type":    "history",
                        "pair":    pair,
                        "candles": candles[-300:],
                    })
                    if len(candles) >= config.MIN_CANDLES:
                        asyncio.create_task(train_models_for_pair(pair))
        except Exception as e:
            log.error("History load error %s %ds: %s", pair, granularity, e)


async def train_models_for_pair(pair: str) -> None:
    candles = candle_store[pair].get(60, [])
    if len(candles) < config.MIN_CANDLES:
        return

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
        "type":     "model_retrained",
        "pair":     pair,
        "accuracy": tab.accuracy,
        "n_candles":tab.n_candles,
    })


# ── Redis Subscriber ─────────────────────────────────────────

async def redis_subscriber() -> None:
    # Always run direct exchange listeners for live ticks.
    # Redis pub/sub is only populated by the Go streamer; without it we need fallbacks.
    asyncio.create_task(direct_deriv_listener())
    asyncio.create_task(direct_binance_listener())

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
    """Subscribe to Deriv tick stream for all forex pairs; builds 1M OHLC locally."""
    import websockets as ws_lib

    url = f"wss://ws.binaryws.com/websockets/v3?app_id={config.DERIVE_APP_ID}"
    try:
        async with ws_lib.connect(url, ping_interval=20, ping_timeout=40) as ws:
            await ws.send(json.dumps({"authorize": config.DERIVE_TOKEN}))
            auth = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if auth.get("error"):
                log.error("Deriv auth failed: %s", auth["error"])
                return

            log.info("Deriv tick listener authorised — subscribing %d pairs", len(config.FOREX_PAIRS))
            for pair in config.FOREX_PAIRS:
                await ws.send(json.dumps({"ticks": pair, "subscribe": 1}))
                await asyncio.sleep(0.05)  # avoid burst rate-limit

            while True:
                raw  = await asyncio.wait_for(ws.recv(), timeout=60)
                data = json.loads(raw)
                if "tick" in data:
                    tick     = data["tick"]
                    pair_sym = str(tick.get("symbol", ""))
                    price    = float(tick.get("quote", 0))
                    epoch    = float(tick.get("epoch", time.time()))
                    if price > 0 and pair_sym:
                        await _process_tick(pair_sym, price, epoch)

    except Exception as e:
        log.error("Direct Deriv listener error: %s — retrying in 10s", e)
        await asyncio.sleep(10)
        asyncio.create_task(direct_deriv_listener())


async def direct_binance_listener() -> None:
    """Subscribe to Binance trade stream for all crypto pairs; build 1M OHLC locally."""
    import websockets as ws_lib

    streams = "/".join(f"{p.lower()}@trade" for p in config.CRYPTO_PAIRS)
    url     = f"wss://stream.binance.com:9443/stream?streams={streams}"

    try:
        async with ws_lib.connect(url, ping_interval=20, ping_timeout=40) as ws:
            log.info("Binance trade listener connected — %d crypto pairs", len(config.CRYPTO_PAIRS))
            while True:
                raw  = await asyncio.wait_for(ws.recv(), timeout=120)
                msg  = json.loads(raw)
                data = msg.get("data") or msg
                if data.get("e") == "trade":
                    pair_sym = str(data.get("s", "")).upper()
                    price    = float(data.get("p", 0))
                    epoch    = float(data.get("T", time.time() * 1000)) / 1000.0
                    if price > 0 and pair_sym in config.CRYPTO_PAIRS:
                        await _process_tick(pair_sym, price, epoch)
    except Exception as e:
        log.error("Binance listener error: %s — retrying in 10s", e)
        await asyncio.sleep(10)
        asyncio.create_task(direct_binance_listener())


async def _process_tick(pair: str, price: float, epoch: float) -> None:
    """Build 1M OHLC candles from raw ticks; emits closed candle + live update."""
    minute_epoch = int(epoch) // 60 * 60  # floor to minute boundary

    builder = _tick_builders.get(pair)

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

    # Emit live (not-yet-closed) candle update so chart stays in sync
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

    # Gap-free candle building
    if store and candle.get("open") is not None:
        last_close = float(store[-1]["close"])
        new_open   = float(candle["open"])
        if abs(new_open - last_close) > last_close * 0.001:
            candle["open"] = last_close
            candle["low"]  = min(float(candle["low"]), last_close)
            candle["high"] = max(float(candle["high"]), last_close)

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

    # Broadcast: always for closed candles; throttle live updates per pair
    is_closed = bool(candle.get("closed", False))
    now_t     = time.time()
    last_t    = _last_live_broadcast.get(pair, 0.0)
    if is_closed or (now_t - last_t) >= _LIVE_BROADCAST_INTERVAL:
        await broadcast({
            "type":        "candle_update",
            "pair":        pair,
            "granularity": granularity,
            "candle":      candle,
        })
        if not is_closed:
            _last_live_broadcast[pair] = now_t

    # Check if 1M candle closed → run signal pipeline
    if granularity == 60 and is_closed:
        await on_1m_close(pair, candle)


async def on_1m_close(pair: str, closed_candle: dict[str, Any]) -> None:
    """Full signal pipeline triggered on every 1M candle close."""
    log.debug("1M close: %s @ %s", pair, closed_candle.get("epoch"))

    candles_1m = candle_store[pair].get(60, [])
    df_1m      = _candles_to_df(candles_1m)

    if len(df_1m) < config.MIN_CANDLES:
        return

    # Signal gate checks
    gate = _check_signal_gates(pair, df_1m)
    if gate:
        await broadcast({
            "type":   "signal_blocked",
            "pair":   pair,
            "reason": gate,
        })
        return

    # Build MTF candle dict
    candle_dfs: dict[int, pd.DataFrame] = {}
    for gran in config.MTF_ANALYSIS_TFS:
        c = candle_store[pair].get(gran, [])
        if c:
            candle_dfs[gran] = _candles_to_df(c)

    utc_hour   = datetime.now(timezone.utc).hour
    utc_minute = datetime.now(timezone.utc).minute

    # Run full analysis
    signal = await _run_signal_pipeline(pair, df_1m, candle_dfs, utc_hour, utc_minute)

    if signal["grade"] != "SKIP":
        # Window selector gate
        ws_sel = window_selectors.get(pair)
        epoch  = int(closed_candle.get("epoch", time.time()))
        mtf    = signal.get("mtf_context", {})
        mtf_ag = float(mtf.get("agreement", 0.5)) if isinstance(mtf, dict) else 0.5
        react  = signal.get("candle_reaction", {})
        net    = float(react.get("net_score", 0)) if isinstance(react, dict) else 0.0

        if ws_sel and not ws_sel.try_add(
            epoch, float(signal["confidence"]), str(signal["signal"]),
            str(signal["grade"]), mtf_ag, net,
        ):
            return  # window full

        signal["window_plan"] = ws_sel.get_window_plan() if ws_sel else []

        await broadcast({"type": "signal", "pair": pair, **signal})

        # Retrain if needed
        tab = tabular_models.get(pair)
        if tab and tab.should_retrain(1):
            asyncio.create_task(train_models_for_pair(pair))

        # Telegram alert
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
    reaction = compute_candle_reaction(df_1m)

    # ── Harmonic Patterns ────────────────────────────────────
    harmonics = detect_harmonics(df_1m)

    # ── MTF Analysis ─────────────────────────────────────────
    mtf = compute_mtf_agreement(candle_dfs, utc_hour)
    if float(mtf.get("agreement", 0)) < config.MTF_MIN_AGREEMENT:
        return _skip_signal("mtf_conflict")

    # ── AI Models ────────────────────────────────────────────
    tab = tabular_models.get(pair)
    tab_result = tab.predict(df_1m, utc_hour) if tab else {}
    tab_probs  = tab_result.get("model_probs", {}) if isinstance(tab_result, dict) else {}
    tab_fused  = float(tab_result.get("fused", 0.5)) if isinstance(tab_result, dict) else 0.5
    tab_dir    = str(tab_result.get("direction", "SKIP")) if isinstance(tab_result, dict) else "SKIP"

    dp = deep_models.get(pair)
    deep_probs = dp.predict(df_1m, utc_hour) if dp else {}
    bayes = dp.predict_bayesian(df_1m, utc_hour) if dp else {"bayes_mean": 0.5, "bayes_std": 0.1}

    # Rule engine
    rule_result = rule_engine_predict(df_1m, smc, inst, reaction, utc_hour)
    rule_dir    = str(rule_result.get("direction", "SKIP"))
    rule_conf   = float(rule_result.get("confidence", 0.5))

    # Combine all model probs
    all_model_probs: dict[str, float] = {}
    all_model_probs.update(tab_probs)  # type: ignore[arg-type]
    all_model_probs.update(deep_probs)
    all_model_probs["rule"] = rule_conf if rule_dir != "SKIP" else 0.5
    all_model_probs["bayes"] = float(bayes.get("bayes_mean", 0.5))

    # Fuse: weighted average
    if all_model_probs:
        model_probs_list = list(all_model_probs.values())
        base_fused = float(np.mean(model_probs_list))
    else:
        base_fused = 0.5

    # Stacking override
    stack_model = stacking_models.get(pair)
    if stack_model and stack_model.trained:
        stack_prob = stack_model.predict(list(all_model_probs.values()))
        base_fused = stack_model.fuse(base_fused, stack_prob)

    # Direction
    direction = "GREEN" if base_fused > 0.5 else "RED"
    if tab_dir == "SKIP" and rule_dir == "SKIP":
        direction = "SKIP"

    if direction == "SKIP":
        return _skip_signal("no_consensus")

    # ── Kalman ───────────────────────────────────────────────
    kf = kalman_filters.get(pair)
    kalman_trend_d = kf.get_trend() if kf else {}
    kt = int(kalman_trend_d.get("kalman_trend", 0))
    kv = float(kalman_trend_d.get("kalman_velocity", 0))
    kalman_agree = (kt == 1 and direction == "GREEN") or (kt == -1 and direction == "RED")

    # ── PPO safety gate ──────────────────────────────────────
    ppo = ppo_agents.get(pair)
    if ppo:
        ppo_dir = ppo.decide(feat, base_fused, direction)
        if ppo_dir == "SKIP":
            return _skip_signal("ppo_skip")

    # ── Meta-labeling ────────────────────────────────────────
    meta = meta_labelers.get(pair)
    meta_prob = meta.predict(feat) if meta else 0.75
    if meta_prob < 0.50:
        return _skip_signal("meta_label_block")

    # ── 4-Layer Scoring ──────────────────────────────────────
    mtf_pts      = mtf_score_pts(mtf)
    smc_pts      = smc_score_pts(smc, inst, wyckoff, harmonics, sd, elliott)
    react_pts    = reaction_score_pts(reaction)
    ai_pts_val   = ai_score_pts(base_fused, meta_prob)

    confidence = score_to_confidence(
        mtf_pts, smc_pts, react_pts, ai_pts_val,
        meta_prob >= 0.60, adx, kalman_agree, kv,
    )

    # Harmonic boost
    h_boost = harmonic_confidence_boost(harmonics, direction)
    confidence = min(confidence + h_boost, 0.99)

    # Consensus filter
    cons_score = consensus_check(feat, direction)
    confidence = apply_consensus_penalty(confidence, cons_score)

    grade_str = grade(confidence)
    if grade_str == "SKIP":
        return _skip_signal("below_threshold")

    # Circuit breaker
    if loss_streak.get(pair, 0) >= 5:
        return _skip_signal("circuit_breaker")

    # Detect patterns list
    from candle_reaction import compute_candle_reaction
    pattern_names: list[str] = []
    if reaction.get("pin_bull"):
        pattern_names.append("pin_bar_bull")
    if reaction.get("pin_bear"):
        pattern_names.append("pin_bar_bear")
    if reaction.get("bull_engulf"):
        pattern_names.append("bullish_engulfing")
    if reaction.get("bear_engulf"):
        pattern_names.append("bearish_engulfing")
    for h_name, h_val in harmonics.items():
        if h_val:
            pattern_names.append(h_name)

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
    if pair not in config.CRYPTO_PAIRS:
        dt  = datetime.now(timezone.utc)
        dow = dt.weekday()  # 0=Mon, 5=Sat, 6=Sun
        h   = dt.hour
        if dow == 4 and h >= 21:
            return "market_closed"
        if dow in (5, 6):
            return "market_closed"

    # News window
    if is_news_window(pair, now):
        return "news_window"

    # Choppy market
    if len(df) >= 20:
        atr   = float((df["high"] - df["low"]).rolling(14).mean().iloc[-1])
        plus_dm  = df["high"].diff().clip(lower=0)
        minus_dm = (-df["low"].diff()).clip(lower=0)
        plus_di  = 100 * plus_dm.rolling(14).mean() / (atr + 1e-10)
        minus_di = 100 * minus_dm.rolling(14).mean() / (atr + 1e-10)
        dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
        adx = float(dx.rolling(14).mean().iloc[-1])
        if adx < 15:
            return "choppy_market"

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
                "type":    "history",
                "pair":    pair,
                "candles": candles[-300:],
            })

        # Send model status
        tab = tabular_models.get(pair)
        await ws.send_json({
            "type":       "model_status",
            "pair":       pair,
            "is_trained": bool(tab and tab.trained),
            "accuracy":   tab.accuracy if tab else 0.0,
            "n_candles":  tab.n_candles if tab else 0,
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
        "note": "Crypto markets trade 24/7",
    }


@app.get("/api/pairs")
async def get_pairs() -> dict[str, Any]:
    return {
        "forex":  config.FOREX_PAIRS,
        "crypto": config.CRYPTO_PAIRS,
        "status": "ok",
    }


@app.get("/api/candles/{pair}")
async def get_candles(pair: str, granularity: int = 60, count: int = 300) -> dict[str, Any]:
    candles = candle_store.get(pair, {}).get(granularity, [])
    return {"pair": pair, "granularity": granularity, "candles": candles[-count:]}


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
