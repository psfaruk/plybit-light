"""PLAYBIT AI — FastAPI backend with WebSocket signal delivery."""

import asyncio
import ctypes
import json
import logging
import platform
import threading
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
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
from telegram_bot import send_signal_alert, update_best_signal, try_send_minute_signal
from timing_engine import aggregate_samples
from window_selector import WindowSelector
from signal_history import SignalHistory

# Broker connector — both modules expose the same API (start_listener,
# fetch_history, get_traders_mood, get_all_sizes, _is_forex_open). Swap to
# `import quotex as quotex_client` once QUOTEX_* env vars are set and the
# pyquotex package is installed (see backend/requirements.txt).
import deriv as quotex_client
import candle_db
from tick_store import tick_store
import rejection_zones as rz_module
import volume_profile as vp_module
from brain_evolution import brain_evolution
from sentiment_engine import SentimentEngine, OrderSizeEngine
from synthetic_sentiment import compute_synthetic_sentiment, score_for_direction as synth_score
from adaptive_threshold import adaptive_threshold
from multi_agent import run_multi_agent
from agent_brain import shared_brain, set_weight_persistence

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

# signal history tracker
signal_history = SignalHistory(max_records=500)

# last broadcast signal per pair — surfaced via /api/signal/{pair}
last_signal_per_pair: dict[str, dict[str, Any]] = {}

# ── New engines (Rejection Zone, Volume Profile, Brain Evolution, Sentiment) ──
rejection_zone_engines: dict[str, rz_module.RejectionZoneEngine] = {}
volume_profile_engine = vp_module.VolumeProfileEngine()   # stateless, one instance
sentiment_engines:    dict[str, SentimentEngine]  = {}
order_size_engines:   dict[str, OrderSizeEngine]  = {}

# tick builders: pair → current 1M candle being assembled from raw ticks
_tick_builders:  dict[str, dict]             = {}

# throttle live tick broadcasts — 16ms ≈ 60fps (matches standard displays)
_last_live_broadcast: dict[str, float]       = {}
_LIVE_BROADCAST_INTERVAL = 0.016

# Live engine snapshot per pair — refreshed every tick, broadcast every ~500ms
live_engine_state: dict[str, dict[str, Any]] = {}
_last_engine_broadcast: dict[str, float] = {}
_ENGINE_BROADCAST_INTERVAL = 0.5   # 2 Hz — fast enough for live UX, light enough on CPU

# Per-signal model probability snapshots (signal_id → list[float])
# Used to train StackingEnsemble after outcomes settle.
signal_model_probs:   dict[str, list[float]] = {}
# Per-pair labeled training buffer: list of (probs, label) pairs
stacking_training_buf: dict[str, list[tuple[list[float], int]]] = {}

# Per-pair wall-clock of the most recent tick — used for connection-quality UI
_last_tick_wallclock: dict[str, float] = {}

# active WebSocket clients
ws_clients: set[WebSocket] = set()

# Captured at startup() so broadcasts dispatched from worker-thread event loops
# (see _run_pipeline_sync) can be scheduled back onto the loop the WebSockets
# were created on. Awaiting ws.send_json from a foreign loop fails silently.
_main_loop: asyncio.AbstractEventLoop | None = None


# ── Lifecycle ────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    candle_db.init_db()

    # Wire agent weight persistence and restore prior learning
    set_weight_persistence(candle_db.save_agent_weight)
    try:
        saved = candle_db.load_agent_weights()
        if saved:
            shared_brain.load_from_db(saved)
    except Exception as _brain_err:
        log.warning("Could not restore agent weights: %s", _brain_err)

    # Initialize per-pair state
    for pair in config.ALL_PAIRS:
        candle_store[pair]     = {}
        kalman_filters[pair]   = KalmanPriceFilter()
        window_selectors[pair] = WindowSelector()
        regime_detectors[pair] = RegimeDetector()
        meta_labelers[pair]    = MetaLabeler(pair)
        ppo_agents[pair]       = PPOAgent()
        stacking_models[pair]  = StackingEnsemble(pair)
        loss_streak[pair]      = 0
        rejection_zone_engines[pair] = rz_module.make_engine(pair)
        sentiment_engines[pair]      = SentimentEngine()
        order_size_engines[pair]     = OrderSizeEngine()
        # Restore stacking training buffer from SQLite
        try:
            stacking_training_buf[pair] = candle_db.load_stacking_buffer(pair)
            if stacking_training_buf[pair]:
                log.info("Loaded %d stacking rows for %s from SQLite",
                         len(stacking_training_buf[pair]), pair)
        except Exception as e:
            log.warning("Could not load stacking buffer for %s: %s", pair, e)

    # Start background tasks
    asyncio.create_task(news_refresher())
    asyncio.create_task(initial_history_loader())
    asyncio.create_task(_telegram_minute_loop())
    asyncio.create_task(_brain_evolution_loop())
    asyncio.create_task(_sentiment_poller())
    _start_boundary_closer_thread(asyncio.get_running_loop())


# ── History Loading ──────────────────────────────────────────

_history_sem = asyncio.Semaphore(1)  # one Deriv history fetch at a time — prevents WS flood
_train_sem   = asyncio.Semaphore(1)  # one ML training job at a time — prevents CPU/RAM crash


async def initial_history_loader() -> None:
    """
    Startup sequence:
      1. Load ALL timeframes from SQLite immediately (instant chart).
      2. Phase-1: gap-fill 1m for all pairs concurrently → broadcast → live chart ready.
      3. Start live tick stream right after Phase-1.
      4. Phase-2: gap-fill remaining TFs in background (no user wait).
    """
    # ── Step 1: SQLite cache → memory (instant, no network) ──────────
    log.info("Loading candles from SQLite cache…")
    for pair in config.ALL_PAIRS:
        for gran in config.CHART_CANDLE_COUNT:
            c = candle_db.load_candles(pair, gran)
            if c:
                candle_store[pair][gran] = c
                if gran == 60:
                    log.info("SQLite: %d × 1m candles for %s", len(c), pair)

    # ── Step 2: Phase-1 — quick 1m preview (200 candles) for each pair ──
    # Sequential + small delay to avoid flooding CPU/RAM at startup.
    log.info("Phase-1: fetching 1m preview candles for all pairs…")
    for pair in config.ALL_PAIRS:
        await _fill_one_tf(pair, 60, quick_count=200)
        await asyncio.sleep(0.5)   # brief yield between pairs
    log.info("Phase-1 complete — live chart ready.")

    # ── Step 3: Start live tick stream ────────────────────────────────
    asyncio.create_task(
        quotex_client.start_listener(config.ALL_PAIRS, _process_tick)
    )
    log.info("Deriv live listener started.")

    # Give the listener 12 s to subscribe all 8 pairs and start receiving ticks.
    await asyncio.sleep(12.0)

    # ── Step 4: Phase-2 — top-up to full 5000 + all other TFs (background) ──
    async def _phase2() -> None:
        # Top up 1m to full 5000 — one pair at a time with breathing room
        for pair in config.ALL_PAIRS:
            await _fill_one_tf(pair, 60)
            await asyncio.sleep(1.0)
        # Then remaining timeframes — add delay between each to avoid RAM/CPU spike
        other_tfs = [g for g in config.CHART_CANDLE_COUNT if g != 60]
        for pair in config.ALL_PAIRS:
            for gran in other_tfs:
                await _fill_one_tf(pair, gran)
                await asyncio.sleep(0.5)
        log.info("Phase-2 complete — all timeframes fully loaded.")

    asyncio.create_task(_phase2())


async def _fill_one_tf(pair: str, granularity: int, quick_count: int = 0) -> None:
    """
    Gap-fill a single pair/timeframe.
    quick_count > 0 → fetch at most that many candles (fast preview mode).
    quick_count = 0 → full gap-fill up to target.
    """
    import time as _time

    target = config.CHART_CANDLE_COUNT.get(granularity, 5000)
    try:
        existing = candle_store[pair].get(granularity) or \
                   candle_db.load_candles(pair, granularity, limit=target)
        candle_store[pair][granularity] = existing or []

        now = _time.time()

        min_required = target // 5  # need at least 20% to skip cold start

        if existing and len(existing) >= min_required:
            gap_candles = int((now - existing[-1]["epoch"]) / granularity)
            if gap_candles <= 1:
                log.info("Up-to-date: %s %ds (%d candles)", pair, granularity, len(existing))
                if granularity == 60:
                    await broadcast({"type": "history", "pair": pair,
                                     "granularity": 60, "candles": existing})
                    # Train from cached data if models not yet loaded
                    if pair not in tabular_models and len(existing) >= config.MIN_CANDLES:
                        asyncio.create_task(train_models_for_pair(pair))
                return
            fetch_count = min(gap_candles + 50, target)
            log.info("Gap-fill: %s %ds — %d candles missing (%.1f h)",
                     pair, granularity, gap_candles, gap_candles * granularity / 3600)
        else:
            fetch_count = target
            log.info("Cold start: %s %ds — fetching %d candles", pair, granularity, target)

        # In quick/preview mode, cap the fetch
        if quick_count > 0:
            fetch_count = min(fetch_count, quick_count)

        async with _history_sem:
            fresh = await quotex_client.fetch_history(pair, granularity, count=fetch_count)

        if not fresh:
            log.warning("No fresh candles for %s %ds", pair, granularity)
            return

        merged = _merge_candles(existing or [], fresh)
        if len(merged) > target:
            merged = merged[-target:]

        candle_store[pair][granularity] = merged
        candle_db.save_candles(pair, granularity, merged)
        log.info("Saved: %d × %ds candles for %s", len(merged), granularity, pair)

        if granularity == 60:
            await broadcast({"type": "history", "pair": pair,
                             "granularity": 60, "candles": merged})
            if len(merged) >= config.MIN_CANDLES:
                asyncio.create_task(train_models_for_pair(pair))

    except Exception as e:
        log.error("Fill error %s %ds: %s", pair, granularity, e)


async def load_history_for_pair(pair: str) -> None:
    """Legacy wrapper — fills all timeframes for a pair."""
    for gran in config.CHART_CANDLE_COUNT:
        await _fill_one_tf(pair, gran)


def _merge_candles(
    existing: list[dict[str, Any]],
    fresh: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge two candle lists, dedup by epoch, return sorted."""
    combined = {float(c["epoch"]): c for c in existing}
    combined.update({float(c["epoch"]): c for c in fresh})
    return sorted(combined.values(), key=lambda x: float(x["epoch"]))


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

        # Tabular training first — lighter, faster
        await asyncio.to_thread(tab.train, df, now_hour)

        # Deep model: skip on first train (cold start) to avoid PyTorch startup
        # hammering CPU alongside 7 other pairs. It will train on the next retrain.
        dp = deep_models.get(pair)
        is_first_train = dp is None
        if dp is None:
            dp = DeepPredictor(pair)
            deep_models[pair] = dp
        if len(candles) >= 100 and not is_first_train:
            await asyncio.to_thread(dp.train, df, now_hour)

        # Small cooldown between pairs so other coroutines get CPU time
        await asyncio.sleep(0.3)

    await broadcast({
        "type":       "model_retrained",
        "pair":       pair,
        "is_trained": bool(tab.trained),
        "accuracy":   tab.accuracy,
        "n_candles":  tab.n_candles,
        "training":   False,
    })


# ── Redis Subscriber ─────────────────────────────────────────




async def _process_tick(pair: str, price: float, epoch: float) -> None:
    """Persist every tick → build 1M OHLC → emit closed + live candle updates."""

    # Track wall-clock of every tick — frontend connection-quality badge reads this
    _last_tick_wallclock[pair] = time.time()

    # 1. Persist raw tick (every single one — never dropped)
    tick_store.append(pair, epoch, price)

    # 2. Aggregate into 1M OHLC builder
    minute_epoch = int(epoch) // 60 * 60  # floor to minute boundary
    builder      = _tick_builders.get(pair)

    # Discard late ticks belonging to an already-closed minute (timer-driven
    # boundary close may have advanced builder while a delayed tick arrives)
    if builder is not None and minute_epoch < builder["minute_epoch"]:
        return

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
        # Start fresh builder for this minute — carry forward the previous
        # close price as the open (matches Quotex), unless this is a cold start
        prev_close = float(builder["close"]) if builder else float(price)
        _tick_builders[pair] = {
            "minute_epoch": minute_epoch,
            "open":  prev_close,
            "high":  max(prev_close, price),
            "low":   min(prev_close, price),
            "close": price,
            "count": 1,
        }
    else:
        # Same minute — update high/low/close (open already set)
        # If timer pre-created the builder with count=0, this is the first
        # real tick; we still keep the carry-forward open.
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

async def on_candle(
    pair: str,
    granularity: int,
    candle: dict[str, Any],
    *,
    force_broadcast: bool = False,
    _skip_pipeline: bool = False,
) -> None:
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

    # Persist ONLY closed candles to SQLite (prevents HTF repaint).
    # Partial higher-TF candles must NOT be persisted — they would corrupt
    # the historical record once the candle eventually closes elsewhere.
    if bool(candle.get("closed", False)):
        candle_db.save_candle(pair, granularity, candle)

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

    # ── Recompute live engine snapshot (synthetic sentiment, VP, RZ) ──
    # Only for 1M granularity (the engines work on 1M data)
    if granularity == 60:
        last_eng_t = _last_engine_broadcast.get(pair, 0.0)
        if is_closed or (now_t - last_eng_t) >= _ENGINE_BROADCAST_INTERVAL:
            try:
                _compute_engine_snapshot(pair, candle)
                snap = live_engine_state.get(pair)
                if snap:
                    await broadcast({
                        "type":   "engine_update",
                        "pair":   pair,
                        "engine": snap,
                    })
                _last_engine_broadcast[pair] = now_t
            except Exception as e:
                log.debug("engine snapshot failed for %s: %s", pair, e)

    if is_closed or force_broadcast or (now_t - last_t) >= _LIVE_BROADCAST_INTERVAL:
        await broadcast({
            "type":        "candle_closed" if is_closed else "candle_update",
            "pair":        pair,
            "granularity": granularity,
            "candle":      candle,
        })
        if not is_closed:
            _last_live_broadcast[pair] = now_t

    # Update rejection zone memory on every 1M close
    if granularity == 60 and is_closed:
        rz_eng = rejection_zone_engines.get(pair)
        if rz_eng:
            candles_1m_now = candle_store[pair].get(60, [])
            if len(candles_1m_now) >= 3:
                rz_eng.update(_candles_to_df(candles_1m_now[-50:]))

    # Check if 1M candle closed → run signal pipeline
    if granularity == 60 and is_closed and not _skip_pipeline:
        await on_1m_close(pair, candle)


# Higher timeframes (in seconds) we keep live by aggregating 1M candles
_HIGHER_TFS = (120, 300, 900, 3600, 14400)


async def _auto_settle_signals(pair: str, just_closed_candle: dict[str, Any]) -> None:
    """
    Compute WIN/LOSS for any pending signal whose trade candle just closed,
    then propagate the outcome to signal_history, brain_evolution, loss_streak,
    and meta_labeler. Triggers stacking training when enough data is settled.
    """
    candles_1m = candle_store.get(pair, {}).get(60, [])
    if len(candles_1m) < 2:
        return

    trade_close_epoch = int(just_closed_candle.get("epoch", 0))
    if trade_close_epoch <= 0:
        return

    # Signal epoch = previous candle (the one that just closed BEFORE this trade candle)
    signal_epoch = trade_close_epoch - 60
    sid = f"{pair}_{signal_epoch}"

    rec = signal_history._records.get(sid)
    if rec is None or rec.outcome is not None:
        return  # no signal at that epoch, or already settled

    # Find analysis candle (the one at signal_epoch — its close was used to make the call)
    analysis = next((c for c in reversed(candles_1m) if int(c.get("epoch", 0)) == signal_epoch), None)
    if analysis is None:
        return
    entry_close = float(analysis.get("close", 0))
    exit_close  = float(just_closed_candle.get("close", 0))

    if entry_close <= 0 or exit_close <= 0:
        return

    direction = rec.direction
    if direction == "GREEN":
        outcome = "WIN" if exit_close > entry_close else ("LOSS" if exit_close < entry_close else "EXPIRED")
    elif direction == "RED":
        outcome = "WIN" if exit_close < entry_close else ("LOSS" if exit_close > entry_close else "EXPIRED")
    else:
        outcome = "EXPIRED"

    # 1. Update signal_history
    signal_history.settle(sid, outcome)

    # 1b. SharedBrain — update each agent's dynamic weight based on outcome
    try:
        shared_brain.record_outcome(pair, signal_epoch, rec.direction, outcome)
    except Exception as _sb_err:
        log.debug("SharedBrain outcome failed: %s", _sb_err)

    # 2. Update brain_evolution (SQLite-backed self-diagnosis)
    try:
        brain_evolution.settle_signal(sid, outcome)
    except Exception as e:
        log.debug("brain_evolution settle failed: %s", e)

    # 3. Update loss_streak circuit breaker
    if outcome == "WIN":
        loss_streak[pair] = 0
    elif outcome == "LOSS":
        loss_streak[pair] = loss_streak.get(pair, 0) + 1

    # 4. Meta-labeler online update
    try:
        meta = meta_labelers.get(pair)
        if meta is not None and hasattr(meta, "record_outcome"):
            meta.record_outcome(float(rec.confidence), outcome == "WIN")
    except Exception:
        pass

    # 4b. Adaptive threshold learns per (pair, session, volatility) bucket
    try:
        # Approximate ADX at signal time from current 1M candle range
        adx_est = 20.0  # safe default
        if len(candles_1m) >= 20:
            recent = candles_1m[-15:]
            ranges = [float(c["high"]) - float(c["low"]) for c in recent]
            avg_r = sum(ranges) / len(ranges)
            last_r = ranges[-1]
            if avg_r > 0:
                adx_est = min(50.0, 14.0 + 30.0 * (last_r / avg_r - 1))
        adaptive_threshold.record_outcome(pair, rec.utc_hour, adx_est, outcome)
    except Exception:
        pass

    log.info("Auto-settled %s: %s (entry=%.5f exit=%.5f streak=%d)",
             sid, outcome, entry_close, exit_close, loss_streak.get(pair, 0))

    # 5. Push to stacking training buffer (label: 1 = WIN, 0 = LOSS)
    if outcome in ("WIN", "LOSS"):
        probs = signal_model_probs.pop(sid, None)
        if probs:
            label = 1 if outcome == "WIN" else 0
            stacking_training_buf.setdefault(pair, []).append((probs, label))
            # cap buffer to last 2000 samples per pair
            if len(stacking_training_buf[pair]) > 2000:
                stacking_training_buf[pair] = stacking_training_buf[pair][-2000:]
            # Persist to SQLite so it survives restarts
            try:
                candle_db.append_stacking_row(pair, probs, label)
            except Exception as e:
                log.debug("stacking_buffer persist error: %s", e)

    # 6. Train stacking if buffer threshold reached
    try:
        await _maybe_train_stacking(pair)
    except Exception as e:
        log.debug("stacking train failed: %s", e)


async def _maybe_train_stacking(pair: str) -> None:
    """Train StackingEnsemble once we have ≥ 50 labeled samples for this pair."""
    buf = stacking_training_buf.get(pair, [])
    if len(buf) < 50 or len(buf) % 25 != 0:
        # Train at thresholds 50, 75, 100, … (avoid per-signal retraining)
        return

    stack = stacking_models.get(pair)
    if stack is None:
        return

    # All snapshots in buf must have the same prob-vector length
    target_n = len(buf[-1][0])
    rows = [(p, y) for p, y in buf if len(p) == target_n]
    if len(rows) < 50:
        return

    import numpy as np
    X = np.array([p for p, _ in rows], dtype=np.float32)
    y = np.array([y for _, y in rows], dtype=np.int32)

    try:
        acc = stack.train(X, y)
        log.info("Stacking trained for %s: %.3f on %d samples", pair, acc, len(rows))
    except Exception as e:
        log.warning("Stacking train error for %s: %s", pair, e)


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
            # Start a new bucket — persist the prior bucket as closed so the
            # live-built HTF history survives a backend restart.
            if store:
                store[-1]["closed"] = True
                try:
                    candle_db.save_candle(pair, tf, store[-1])
                except Exception as e:
                    log.debug("HTF persist failed for %s/%ds: %s", pair, tf, e)
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

    # ── Auto-settle the trade candle from the PREVIOUS minute's signal ──
    # Signal generated when candle N closed picks candle N+1 (this candle)
    # as the trade candle. So when N+1 closes, we can WIN/LOSS the signal.
    await _auto_settle_signals(pair, closed_candle)

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

    # Build MTF candle dict — closed candles ONLY (prevents HTF repaint).
    # We drop the trailing open candle so signal decisions don't depend on a
    # bar that will mutate over the next few minutes.
    candle_dfs: dict[int, pd.DataFrame] = {}
    for gran in config.MTF_ANALYSIS_TFS:
        c = candle_store[pair].get(gran, [])
        if c:
            # Strip the last bar if it is still open
            if c and not bool(c[-1].get("closed", False)):
                c = c[:-1]
            if c:
                n = config.ANALYSIS_CANDLE_COUNT.get(gran, 200)
                candle_dfs[gran] = _candles_to_df(c[-n:])

    utc_hour   = datetime.now(timezone.utc).hour
    utc_minute = datetime.now(timezone.utc).minute

    # Run full analysis — strict pipeline per Master Plan §18
    epoch_int = int(closed_candle.get("epoch", 0))
    signal = await _run_signal_pipeline(pair, df_1m, candle_dfs, utc_hour, utc_minute, epoch_int)

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
    signal["signal_id"]         = f"{pair}_{epoch}"

    # Settle any prior pending signals regardless of whether this one emits.
    signal_history.auto_settle_expired(pair, epoch)

    # Candle-close execution guard: skip if pipeline took > SIGNAL_MAX_DELAY_SEC.
    # Must run BEFORE recording — otherwise an unbroadcast signal still settles
    # as WIN/LOSS next candle and corrupts history / model training.
    candle_close_wall = epoch + 60
    pipeline_lag = time.time() - candle_close_wall
    if pipeline_lag > config.SIGNAL_MAX_DELAY_SEC:
        log.info("Signal expired for %s — pipeline lag %.1fs > %ds limit",
                 pair, pipeline_lag, config.SIGNAL_MAX_DELAY_SEC)
        await broadcast({
            "type":   "signal_blocked",
            "pair":   pair,
            "reason": "signal_expired",
        })
        return

    signal_history.add(
        signal_id=signal["signal_id"],
        pair=pair,
        direction=str(signal["signal"]),
        confidence=float(signal["confidence"]),
        grade=str(signal["grade"]),
        epoch=epoch,
        utc_hour=utc_hour,
    )
    # Also record in Brain Evolution SQLite for self-diagnosis
    brain_evolution.record_signal(
        signal_id=signal["signal_id"],
        pair=pair,
        direction=str(signal["signal"]),
        confidence=float(signal["confidence"]),
        grade=str(signal["grade"]),
        epoch=epoch,
        utc_hour=utc_hour,
    )

    # Snapshot model probs for later stacking training when outcome settles
    try:
        ai_models = signal.get("ai_models", {}) or {}
        if isinstance(ai_models, dict) and ai_models:
            signal_model_probs[signal["signal_id"]] = [
                float(v) for v in ai_models.values()
            ]
    except Exception:
        pass

    last_signal_per_pair[pair] = {**signal, "pair": pair}

    await broadcast({"type": "signal", "pair": pair, **signal})

    # Retrain if needed
    tab = tabular_models.get(pair)
    if tab and tab.should_retrain(1):
        asyncio.create_task(train_models_for_pair(pair))

    # Telegram — register with per-minute best-signal tracker
    update_best_signal(dict(signal), pair)


async def _run_signal_pipeline(
    pair: str,
    df_1m: pd.DataFrame,
    candle_dfs: dict[int, pd.DataFrame],
    utc_hour: int,
    utc_minute: int,
    epoch: int = 0,
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
    advanced      = compute_all_advanced(df_1m)
    wyckoff       = {k: int(v)   for k, v in advanced.items() if k.startswith("wyckoff")}
    elliott       = {k: float(v) for k, v in advanced.items() if k.startswith("elliott")}
    sd            = {k: float(v) for k, v in advanced.items() if k.startswith("sd_")}
    chart_pats    = {k: float(v) for k, v in advanced.items() if k.startswith("chart_")}

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

    # ── Direction: multi-source consensus vote ──────────────────
    # Accumulate weighted evidence for GREEN vs RED.
    # No single source can override the rest; tie → price momentum.
    _green_wt = 0.0
    _red_wt   = 0.0

    # AI vote — only when clearly biased (outside 45–55% band)
    _ai_deviation = abs(base_fused - 0.50)
    if base_fused >= 0.55:
        _green_wt += _ai_deviation * 2.0 * 1.5   # max contribution ~0.75 at fused=0.75
    elif base_fused <= 0.45:
        _red_wt   += _ai_deviation * 2.0 * 1.5
    # 0.45–0.55: AI uncertain → no vote

    # Rule engine
    if rule_dir == "GREEN":
        _green_wt += rule_conf * 1.2
    elif rule_dir == "RED":
        _red_wt   += rule_conf * 1.2

    # MTF directional weight
    if mtf_dir_str == "bull":
        _green_wt += mtf_agreement * 1.0
    elif mtf_dir_str == "bear":
        _red_wt   += mtf_agreement * 1.0

    # ADX directional indicator (di_diff = +DI − −DI)
    _di_diff = float(feat.get("di_diff", 0))
    if abs(_di_diff) > 8:
        _di_strength = min(abs(_di_diff) / 50.0, 0.8)
        if _di_diff > 0:
            _green_wt += _di_strength
        else:
            _red_wt   += _di_strength

    # SMC net score (positive = bullish zone context, negative = bearish)
    _smc_net = float(smc.get("smc_net_score", 0))
    if abs(_smc_net) > 15:
        _smc_strength = min(abs(_smc_net) / 60.0, 0.7)
        if _smc_net > 0:
            _green_wt += _smc_strength
        else:
            _red_wt   += _smc_strength

    if _green_wt > _red_wt:
        direction = "GREEN"
    elif _red_wt > _green_wt:
        direction = "RED"
    elif len(df_1m) >= 2:
        _last_c = float(df_1m["close"].iloc[-1])
        _prev_c = float(df_1m["close"].iloc[-2])
        direction = "GREEN" if _last_c >= _prev_c else "RED"
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
    mtf_pts    = mtf_score_pts(mtf, direction=direction)
    smc_pts    = smc_score_pts(smc, inst, wyckoff, harmonics, sd, elliott, direction=direction, chart_patterns=chart_pats)
    react_pts  = reaction_score_pts(reaction, direction=direction)
    # Align AI confidence to signal direction before scoring (base_fused is GREEN prob)
    _ai_conf_aligned = base_fused if direction == "GREEN" else (1.0 - base_fused)
    ai_pts_val = ai_score_pts(_ai_conf_aligned, meta_prob)

    confidence = score_to_confidence(
        mtf_pts, smc_pts, react_pts, ai_pts_val,
        meta_prob >= 0.60, adx, kalman_agree, kv,
    )

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
    # MTF opposition — grade by how many senior TFs disagree
    _tfs_d     = mtf.get("tfs", {})
    _h1_bias   = str(_tfs_d.get("1h",  {}).get("bias", "neutral")) if isinstance(_tfs_d.get("1h"),  dict) else "neutral"  # type: ignore[union-attr]
    _m15_bias  = str(_tfs_d.get("15m", {}).get("bias", "neutral")) if isinstance(_tfs_d.get("15m"), dict) else "neutral"  # type: ignore[union-attr]
    _opposing_h1  = (direction == "GREEN" and _h1_bias  == "bear") or (direction == "RED" and _h1_bias  == "bull")
    _opposing_m15 = (direction == "GREEN" and _m15_bias == "bear") or (direction == "RED" and _m15_bias == "bull")
    if _opposing_h1 and _opposing_m15:
        confidence *= 0.72   # both senior TFs oppose — moderate block
    elif _opposing_h1 or _opposing_m15:
        confidence *= 0.85   # one senior TF opposes — soft penalty
    elif mtf_dir_str != "neutral":
        if (direction == "GREEN" and mtf_dir_str == "bear") or \
           (direction == "RED"   and mtf_dir_str == "bull"):
            confidence *= 0.93  # only lower TFs oppose — mild penalty

    # Majority-vote gate: if ≥60% of all models oppose direction, penalise
    _all_probs = list(all_model_probs.values())
    if _all_probs:
        _oppose_count = sum(1 for p in _all_probs if (direction == "GREEN" and p < 0.50) or (direction == "RED" and p > 0.50))
        if _oppose_count / len(_all_probs) >= 0.60:
            confidence *= 0.85

    if mtf_agreement < config.MTF_MIN_AGREEMENT:
        confidence *= 0.97
    if ppo_skip:
        confidence *= 0.96
    if meta_prob < 0.60:
        confidence *= 0.97
    # Note: ADX < 15 already penalised inside score_to_confidence; no second pass here.

    # ATR-spike: large candle = institutional move already in progress.
    # Reduce confidence rather than trade into a fast-moving candle.
    if len(df_1m) >= 21:
        try:
            atr_series_p   = (df_1m["high"] - df_1m["low"]).rolling(14).mean()
            current_range  = float(df_1m["high"].iloc[-1] - df_1m["low"].iloc[-1])
            prev_avg_range = float(atr_series_p.iloc[-2])
            if prev_avg_range > 0:
                _atr_ratio = current_range / (prev_avg_range + 1e-10)
                if _atr_ratio > 3.0:
                    confidence *= 0.65   # very large spike
                elif _atr_ratio > 2.0:
                    confidence *= 0.80   # moderate spike
        except Exception:
            pass

    # ── Rejection Zone Engine ────────────────────────────────
    current_price = float(df_1m["close"].iloc[-1])
    rz_eng  = rejection_zone_engines.get(pair)
    rz_info = rz_eng.summary(current_price) if rz_eng else {}
    rz_score = float(rz_info.get("rz_score", 0))
    rz_sig   = rz_info.get("rz_signal", "NONE")

    if rz_score >= 65:
        if rz_sig == direction:
            # Strong zone confirms our direction — boost
            confidence = min(confidence * (1.0 + rz_score / 1000), 0.99)
        elif rz_sig != "NONE" and rz_sig != direction:
            # Zone opposes direction — penalty
            confidence *= (1.0 - rz_score / 500)

    # ── Volume Profile Engine ─────────────────────────────────
    vp_info = volume_profile_engine.compute(df_1m, current_price)
    vp_zone = vp_info.get("vp_zone", "NEUTRAL")
    vp_at_poc = bool(vp_info.get("vp_at_poc", False))

    if vp_zone == "AT_RESISTANCE" and direction == "RED":
        confidence = min(confidence * 1.05, 0.99)   # selling at resistance ✓
    elif vp_zone == "AT_SUPPORT" and direction == "GREEN":
        confidence = min(confidence * 1.05, 0.99)   # buying at support ✓
    elif vp_zone == "AT_RESISTANCE" and direction == "GREEN":
        confidence *= 0.90   # buying into resistance ✗
    elif vp_zone == "AT_SUPPORT" and direction == "RED":
        confidence *= 0.90   # selling into support ✗
    elif vp_zone == "LVN_FAST_MOVE":
        confidence = min(confidence * 1.03, 0.99)   # LVN = fast directional move
    if vp_at_poc:
        confidence *= 0.97   # at POC = two-way, slight caution

    # ── Sentiment Engine (broker mood — contrarian; None on Deriv) ──
    sent_eng = sentiment_engines.get(pair)
    sent_info = sent_eng.score(direction) if sent_eng else {"sent_mult": 1.0}
    confidence *= float(sent_info.get("sent_mult", 1.0))

    # ── Synthetic Sentiment (price-action derived; ALWAYS active) ──
    synth_info  = compute_synthetic_sentiment(df_1m, lookback=30)
    synth_apply = synth_score(synth_info, direction)
    confidence *= float(synth_apply.get("synth_mult", 1.0))
    synth_info.update(synth_apply)

    # ── Order Size / Volume Imbalance ────────────────────────────
    os_eng  = order_size_engines.get(pair)
    os_info = os_eng.features() if os_eng else {}
    os_imb  = float(os_info.get("os_imbalance", 0.0))
    # Imbalance aligned with direction = boost (institutional flow confirms)
    if direction == "GREEN" and os_imb > 0.2:
        confidence = min(confidence * 1.04, 0.99)
    elif direction == "RED" and os_imb < -0.2:
        confidence = min(confidence * 1.04, 0.99)
    elif direction == "GREEN" and os_imb < -0.2:
        confidence *= 0.94
    elif direction == "RED" and os_imb > 0.2:
        confidence *= 0.94

    # ── Brain Evolution multiplier (learned hour-based penalty) ──
    brain_mult = brain_evolution.get_confidence_multiplier(pair, utc_hour)
    if brain_mult != 1.0:
        confidence *= brain_mult

    confidence = max(0.0, min(confidence, 0.99))

    grade_str = grade(confidence)
    if grade_str == "SKIP":
        return _skip_signal("low_score")

    # Adaptive per-pair / session / volatility threshold — tightens automatically
    # if win rate has been poor in this regime.
    adaptive_thr = adaptive_threshold.get_threshold(pair, utc_hour, adx)
    if confidence < adaptive_thr:
        return _skip_signal(f"below_adaptive_thr_{adaptive_thr:.2f}")

    # Plan §18: 5 consecutive losses → 30 min pause (kept as hard block)
    if loss_streak.get(pair, 0) >= 5:
        return _skip_signal("circuit_break")

    # ── Multi-Agent Consensus Engine ─────────────────────────────
    # All specialized agents analyze independently, write to SharedBrain,
    # read each other's results, then Critic + MetaController produce
    # the final adjusted direction + confidence.
    multi = {}
    try:
        multi = run_multi_agent(
            pair=pair, epoch=epoch, df_1m=df_1m,
            feat=feat, adx=adx,
            smc=smc, inst=inst,
            reaction=reaction, mtf=mtf,
            base_fused=base_fused,
            all_model_probs=all_model_probs,
            vp_info=vp_info, os_info=os_info,
            sent_info=sent_info, synth_info=synth_info,
            rz_info=rz_info,
            pipeline_dir=direction,
            pipeline_conf=confidence,
            utc_hour=utc_hour,
        )
        # Use multi-agent adjusted values
        direction  = multi["final_direction"]
        confidence = multi["final_confidence"]
        # Re-grade with new confidence
        grade_str = grade(confidence)
        if grade_str == "SKIP":
            return _skip_signal("multi_agent_below_threshold")
    except Exception as _ma_err:
        log.warning("MultiAgent failed for %s: %s — using pipeline values", pair, _ma_err)

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
        "institutional":    inst,
        "wyckoff":          wyckoff,
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
        "rejection_zone":      rz_info,
        "volume_profile":      vp_info,
        "sentiment":           sent_info,
        "order_flow":          os_info,
        "synthetic_sentiment": synth_info,
        # Multi-agent consensus data
        "multi_agent": {
            "regime":         multi.get("regime", "unknown"),
            "conflict_score": multi.get("conflict_score", 0),
            "agreement":      multi.get("agreement", 0.5),
            "dir_changed":    multi.get("dir_changed", False),
            "critique":       multi.get("critique", []),
            "green_weight":   multi.get("green_weight", 0),
            "red_weight":     multi.get("red_weight", 0),
            "agent_report":   multi.get("agent_report", {}),
        } if multi else None,
    }


def _check_signal_gates(pair: str, df: pd.DataFrame) -> str:
    """Returns non-empty string (reason) if signal should be blocked."""
    now = time.time()

    # Deriv Forex runs 24/5 — no market_closed gate needed here (news gate below).

    # News window ±15 min — hard block for high-impact whipsaw risk.
    if is_news_window(pair, now):
        return "news_window"

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


async def _broadcast_impl(msg: dict[str, Any]) -> None:
    # Snapshot the set before iterating — connect/disconnect on other tasks
    # mutate ws_clients concurrently, which previously raised "Set changed
    # size during iteration" and interrupted live tick delivery.
    snapshot = list(ws_clients)
    dead: list[WebSocket] = []
    for ws in snapshot:
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_clients.discard(ws)


async def broadcast(msg: dict[str, Any]) -> None:
    # _run_pipeline_sync runs on_1m_close inside a worker-thread event loop,
    # but ws.send_json must be awaited on the loop the WebSocket was created
    # on. Detect the foreign-loop case and dispatch back to the main loop.
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    if _main_loop is not None and running is not _main_loop:
        fut = asyncio.run_coroutine_threadsafe(_broadcast_impl(msg), _main_loop)
        try:
            await asyncio.wrap_future(fut)
        except Exception as e:
            log.debug("cross-loop broadcast failed: %s", e)
        return

    await _broadcast_impl(msg)


# ── Background Tasks ─────────────────────────────────────────

async def news_refresher() -> None:
    while True:
        try:
            await fetch_news_events()
        except Exception:
            pass
        await asyncio.sleep(3600)


async def _telegram_minute_loop() -> None:
    """Every 5s: send best signal of the past minute to Telegram."""
    while True:
        await asyncio.sleep(5)
        try:
            await try_send_minute_signal()
        except Exception as e:
            log.warning("Telegram minute loop error: %s", e)


async def _brain_evolution_loop() -> None:
    """Every hour: check if self-diagnosis is due (throttled internally to 6h)."""
    while True:
        await asyncio.sleep(3600)
        try:
            brain_evolution.maybe_run_diagnosis()
        except Exception as e:
            log.warning("Brain evolution loop error: %s", e)


async def _sentiment_poller() -> None:
    """Every 2s: pull traders-mood + order-size from Quotex into engines."""
    while True:
        await asyncio.sleep(2.0)
        try:
            for pair in config.ALL_PAIRS:
                mood = quotex_client.get_traders_mood(pair)
                if mood:
                    sentiment_engines[pair].update(mood)
                sizes = quotex_client.get_all_sizes(pair)
                if sizes:
                    order_size_engines[pair].update(sizes)
        except Exception as e:
            log.debug("Sentiment poller error: %s", e)


async def _close_candles_at_boundary() -> None:
    """
    Coroutine: close the previous minute's builders and open new ones.
    Phase 1 (sequential, ~1ms): close all 8 candles and open new bars.
    Phase 2 (concurrent): run all 8 signal pipelines simultaneously.
    Called from the precision boundary thread via run_coroutine_threadsafe.
    """
    now            = time.time()
    current_minute = int(now) // 60 * 60
    prev_minute    = current_minute - 60

    pairs_to_signal: list[tuple[str, dict]] = []

    # ── Phase 1: close + open all candles (fast, sequential) ─────────
    for pair in list(_tick_builders.keys()):
        builder = _tick_builders.get(pair)
        if builder is None:
            continue
        if builder["minute_epoch"] != prev_minute:
            continue

        try:
            closed_candle = {
                "epoch":  float(prev_minute),
                "open":   builder["open"],
                "high":   builder["high"],
                "low":    builder["low"],
                "close":  builder["close"],
                "closed": True,
            }
            # Update candle store + HTF aggregation (no signal pipeline yet)
            await on_candle(pair, 60, closed_candle, _skip_pipeline=True)

            carry = builder["close"]
            _tick_builders[pair] = {
                "minute_epoch": current_minute,
                "open":  carry, "high": carry,
                "low":   carry, "close": carry,
                "count": 0,
            }
            await on_candle(pair, 60, {
                "epoch":  float(current_minute),
                "open":   carry, "high": carry,
                "low":    carry, "close": carry,
                "closed": False,
            }, force_broadcast=True)

            pairs_to_signal.append((pair, closed_candle))
        except Exception as e:
            log.debug("boundary close failed for %s: %s", pair, e)

    # ── Phase 2: run all signal pipelines concurrently ───────────────
    if pairs_to_signal:
        loop = asyncio.get_event_loop()
        await asyncio.gather(*[
            loop.run_in_executor(None, _run_pipeline_sync, pair, candle)
            for pair, candle in pairs_to_signal
        ], return_exceptions=True)


def _run_pipeline_sync(pair: str, closed_candle: dict) -> None:
    """Thread-pool wrapper: runs on_1m_close in a background thread."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(on_1m_close(pair, closed_candle))
        loop.close()
    except Exception as e:
        log.warning("Pipeline thread error for %s: %s", pair, e)


def _set_windows_timer_resolution(ms: int = 1) -> None:
    """Request 1 ms timer resolution on Windows for precise sleep()."""
    if platform.system() != "Windows":
        return
    try:
        ctypes.windll.winmm.timeBeginPeriod(ms)
        log.info("Windows timer resolution set to %d ms", ms)
    except Exception as e:
        log.debug("timeBeginPeriod failed: %s", e)


def _start_boundary_closer_thread(loop: asyncio.AbstractEventLoop) -> None:
    """
    Spin up a dedicated OS thread that fires at EXACTLY the minute boundary.

    Why a thread instead of asyncio.sleep?
    asyncio.sleep is at the mercy of the event loop — if a training job or
    8-pair tick burst is running, the sleep can be 200 ms–2 s late.
    A dedicated thread using time.sleep (backed by the OS scheduler) fires
    within ~2 ms on Linux and ~1 ms on Windows once we set timeBeginPeriod(1).
    The last 5 ms uses a spin-wait to get ~0.5 ms accuracy.
    """
    _set_windows_timer_resolution(1)

    def _run() -> None:
        import sys
        log.info("Candle boundary thread started (precision ~1ms)")
        while True:
            try:
                now           = time.time()
                next_boundary = (int(now) // 60 + 1) * 60

                # Coarse sleep: wake 10 ms before the boundary
                coarse = next_boundary - now - 0.010
                if coarse > 0:
                    time.sleep(coarse)

                # Fine spin-wait for the final 10 ms — burns <0.1% CPU once per minute
                while time.time() < next_boundary:
                    time.sleep(0.001)

                # Precise moment — fire and forget (don't block the thread waiting
                # for ML pipeline which can take 10+ seconds)
                fire_at = time.time()
                try:
                    asyncio.run_coroutine_threadsafe(_close_candles_at_boundary(), loop)
                except BaseException as ex:
                    log.warning("Boundary submit error: %s", ex)

                ms_late = (fire_at - next_boundary) * 1000
                log.info("Boundary fired %.1f ms past :00", ms_late)
                sys.stderr.flush()

            except BaseException as e:
                log.error("Boundary thread error: %s", e, exc_info=True)
                sys.stderr.flush()
                time.sleep(1.0)

    t = threading.Thread(target=_run, daemon=True, name="candle-boundary")
    t.start()


# ── REST Endpoints ───────────────────────────────────────────

@app.get("/api/market-hours")
async def get_market_hours() -> dict[str, Any]:
    """Deriv Forex market hours."""
    now     = datetime.now(timezone.utc)
    is_live = quotex_client._is_forex_open()
    mode    = "live" if is_live else "closed"
    return {
        "is_open":     is_live,
        "mode":        mode,
        "current_utc": now.strftime("%Y-%m-%d %H:%M UTC"),
        "note":        "Deriv Forex — live Sun 21:00 UTC through Fri 21:00 UTC",
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


@app.get("/api/engine_state/{pair}")
async def get_engine_state(pair: str) -> dict[str, Any]:
    """Current live engine snapshot — refreshed on every tick."""
    return {"pair": pair, "engine": live_engine_state.get(pair, {})}


@app.get("/api/adaptive_threshold")
async def get_adaptive_threshold() -> dict[str, Any]:
    """Learned per-(pair, session, vol) confidence thresholds."""
    return {"thresholds": adaptive_threshold.snapshot()}


@app.get("/api/sentiment/{pair}")
async def get_sentiment(pair: str) -> dict[str, Any]:
    """Live trader sentiment + order flow."""
    eng = sentiment_engines.get(pair)
    oe  = order_size_engines.get(pair)
    return {
        "pair":         pair,
        "raw_mood":     quotex_client.get_traders_mood(pair),
        "raw_size":     quotex_client.get_all_sizes(pair),
        "fresh_mood":   eng.is_fresh() if eng else False,
        "buyer_ratio":  eng.buyer_ratio if eng else None,
        "extreme":      eng.crowd_extreme if eng else None,
        "velocity":     eng.velocity if eng else None,
        "order_flow":   oe.features() if oe else None,
    }


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


@app.post("/api/result/{signal_id}/settle")
async def settle_signal(signal_id: str, outcome: str) -> dict[str, Any]:
    """Manually settle a signal: outcome = WIN | LOSS | EXPIRED."""
    outcome = outcome.upper()
    ok = signal_history.settle(signal_id, outcome)
    if ok:
        try:
            brain_evolution.settle_signal(signal_id, outcome)
        except Exception:
            pass
        # Loss streak update for manual settlement too
        rec = signal_history._records.get(signal_id)
        if rec and rec.pair:
            if outcome == "WIN":
                loss_streak[rec.pair] = 0
            elif outcome == "LOSS":
                loss_streak[rec.pair] = loss_streak.get(rec.pair, 0) + 1
    return {"signal_id": signal_id, "outcome": outcome, "found": ok}


@app.get("/api/signal-history/{pair}")
async def get_signal_history(pair: str, limit: int = 50) -> dict[str, Any]:
    return {"pair": pair, "records": signal_history.get_records(pair, limit=limit)}


@app.get("/api/stats/{pair}")
async def get_pair_stats(pair: str) -> dict[str, Any]:
    return signal_history.get_stats(pair)


# ── Helpers ──────────────────────────────────────────────────

def _compute_engine_snapshot(pair: str, live_candle: dict[str, Any]) -> None:
    """
    Refresh live_engine_state[pair] with current engine outputs.
    Cheap to call — used to broadcast ~2 Hz live UX updates.
    """
    candles_1m = candle_store.get(pair, {}).get(60, [])
    if len(candles_1m) < 10:
        return

    # Splice in the in-progress live candle so synthetic sentiment sees it
    df_1m   = _candles_to_df(candles_1m[-100:])
    current_price = float(live_candle.get("close", df_1m["close"].iloc[-1]))

    # Rejection Zone — already updated on candle_close; just query for live price
    rz_eng = rejection_zone_engines.get(pair)
    rz_info = rz_eng.summary(current_price) if rz_eng else {}

    # Volume Profile — lightweight enough to recompute
    vp_info = volume_profile_engine.compute(df_1m, current_price)

    # Sentiment (broker)
    sent_eng = sentiment_engines.get(pair)
    sent_buyer = sent_eng.buyer_ratio if sent_eng else 0.5
    sent_extreme = sent_eng.crowd_extreme if sent_eng else "NEUTRAL"
    sent_fresh = sent_eng.is_fresh() if sent_eng else False

    # Synthetic sentiment — recompute on every snapshot
    from synthetic_sentiment import compute_synthetic_sentiment
    synth = compute_synthetic_sentiment(df_1m, lookback=30)

    # Order flow
    os_eng = order_size_engines.get(pair)
    of_info = os_eng.features() if os_eng else {"os_total":0, "os_buy_pct":0.5, "os_imbalance":0}

    last_tick = _last_tick_wallclock.get(pair, 0.0)
    tick_age  = time.time() - last_tick if last_tick > 0 else 999.0

    live_engine_state[pair] = {
        "rejection_zone":      rz_info,
        "volume_profile":      vp_info,
        "synthetic_sentiment": synth,
        "sentiment": {
            "buyer_ratio":  sent_buyer,
            "extreme":      sent_extreme,
            "fresh":        sent_fresh,
        },
        "order_flow":          of_info,
        "current_price":       current_price,
        "tick_age":            round(tick_age, 1),
        "ts":                  time.time(),
    }


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
