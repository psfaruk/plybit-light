"""PLAYBIT AI — FastAPI backend with WebSocket signal delivery."""

import asyncio
import glob
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import redis.asyncio as aioredis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

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
from tick_store import tick_store
from price_hold import analyze_price_hold

import deriv as deriv_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("playbit")

app = FastAPI(title="PLAYBIT AI", version="6.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── State ────────────────────────────────────────────────────────────

redis_client: aioredis.Redis | None = None
candle_store: dict[str, dict[int, list[dict[str, Any]]]] = {}
tabular_models:  dict[str, TabularPredictor] = {}
deep_models:     dict[str, DeepPredictor]    = {}
stacking_models: dict[str, StackingEnsemble] = {}
ppo_agents:      dict[str, PPOAgent]         = {}
meta_labelers:   dict[str, MetaLabeler]      = {}
kalman_filters:  dict[str, KalmanPriceFilter]= {}
regime_detectors:dict[str, RegimeDetector]   = {}
window_selectors:dict[str, WindowSelector]   = {}
loss_streak:     dict[str, int]              = {}
ws_clients: set[WebSocket] = set()


# ── Lifecycle ────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    global redis_client

    # Clear stale model files when CLEAR_MODELS=true (set once in Railway env after label fix)
    if os.getenv("CLEAR_MODELS", "").lower() in ("1", "true", "yes"):
        for pattern in ["models/*.joblib", "models/*.pt", "models/*.pkl"]:
            for f in glob.glob(pattern):
                try:
                    os.remove(f)
                    log.info("Cleared old model: %s", f)
                except Exception:
                    pass
        log.info("Old models cleared — will retrain with correct labels.")

    try:
        redis_client = aioredis.from_url(config.REDIS_URL, decode_responses=True)
        await redis_client.ping()
        log.info("Redis connected: %s", config.REDIS_URL)
    except Exception as e:
        log.warning("Redis unavailable (%s) — running without streamer", e)
        redis_client = None

    for pair in config.ALL_PAIRS:
        candle_store[pair]     = {}
        kalman_filters[pair]   = KalmanPriceFilter()
        window_selectors[pair] = WindowSelector()
        regime_detectors[pair] = RegimeDetector()
        meta_labelers[pair]    = MetaLabeler()
        ppo_agents[pair]       = PPOAgent()
        stacking_models[pair]  = StackingEnsemble()
        loss_streak[pair]      = 0

    asyncio.create_task(redis_subscriber())
    asyncio.create_task(news_refresher())
    asyncio.create_task(initial_history_loader())


# ── History Loading ───────────────────────────────────────────────────

async def initial_history_loader() -> None:
    log.info("Loading historical candles…")
    tasks: list[asyncio.Task[None]] = []
    for pair in config.ALL_PAIRS:
        tasks.append(asyncio.create_task(load_history_for_pair(pair)))
    await asyncio.gather(*tasks, return_exceptions=True)
    log.info("Historical load complete.")


async def load_history_for_pair(pair: str) -> None:
    for granularity in list(config.CANDLE_COUNT.keys()):
        try:
            candles = await deriv_client.fetch_history(pair, granularity)
            if candles:
                candle_store[pair][granularity] = candles
                log.debug("Loaded %d × %ds candles for %s", len(candles), granularity, pair)
                if granularity == 60 and len(candles) >= config.MIN_CANDLES:
                    asyncio.create_task(train_models_for_pair(pair))
        except Exception as e:
            log.error("History load error %s %ds: %s", pair, granularity, e)


async def train_models_for_pair(pair: str) -> None:
    candles = candle_store[pair].get(60, [])
    if len(candles) < config.MIN_CANDLES:
        return
    df = _candles_to_df(candles)
    now_hour = datetime.now(timezone.utc).hour

    tab = tabular_models.get(pair)
    if tab is None:
        tab = TabularPredictor(pair, 60)
        tabular_models[pair] = tab
    tab.train(df, now_hour)

    dp = deep_models.get(pair)
    if dp is None:
        dp = DeepPredictor(pair)
        deep_models[pair] = dp
    if len(candles) >= 100:
        dp.train(df, now_hour)

    await broadcast({
        "type":     "model_retrained",
        "pair":     pair,
        "accuracy": tab.accuracy,
        "n_candles":tab.n_candles,
    })


# ── Redis Subscriber ──────────────────────────────────────────────────

async def redis_subscriber() -> None:
    if redis_client is None:
        log.info("Redis not available — starting Deriv WebSocket listener instead")
        asyncio.create_task(direct_deriv_listener())
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
    import websockets as ws_lib
    url = f"wss://ws.binaryws.com/websockets/v3?app_id={config.DERIVE_APP_ID}"
    try:
        async with ws_lib.connect(url) as ws:
            await ws.send(json.dumps({"authorize": config.DERIVE_TOKEN}))
            await ws.recv()
            for pair in config.FOREX_PAIRS[:5]:
                await ws.send(json.dumps({
                    "ticks_history": pair, "subscribe": 1,
                    "end": "latest", "count": 1, "granularity": 60, "style": "candles",
                }))
            while True:
                raw  = await ws.recv()
                data = json.loads(raw)
                if "ohlc" in data:
                    ohlc = data["ohlc"]
                    pair_sym = str(ohlc.get("symbol", ""))
                    candle   = {
                        "epoch":  float(ohlc["open_time"]),
                        "open":   float(ohlc["open"]),
                        "high":   float(ohlc["high"]),
                        "low":    float(ohlc["low"]),
                        "close":  float(ohlc["close"]),
                        "closed": bool(ohlc.get("close_time", 0) < time.time()),
                    }
                    await on_candle(pair_sym, 60, candle)
    except Exception as e:
        log.error("Direct Deriv listener error: %s — retrying in 5s", e)
        await asyncio.sleep(5)
        asyncio.create_task(direct_deriv_listener())


# ── Candle Processing ──────────────────────────────────────────────────

async def on_candle(pair: str, granularity: int, candle: dict[str, Any]) -> None:
    if pair not in candle_store:
        candle_store[pair] = {}
    store = candle_store[pair].setdefault(granularity, [])
    if store and candle.get("open") is not None:
        last_close = float(store[-1]["close"])
        new_open   = float(candle["open"])
        if abs(new_open - last_close) > last_close * 0.001:
            candle["open"] = last_close
            candle["low"]  = min(float(candle["low"]), last_close)
            candle["high"] = max(float(candle["high"]), last_close)
    if store and store[-1]["epoch"] == candle["epoch"]:
        store[-1] = candle
    else:
        store.append(candle)
    if len(store) > config.HISTORY_COUNT:
        candle_store[pair][granularity] = store[-config.HISTORY_COUNT:]
    if granularity == 60:
        # Record 1M price updates as ticks (covers Redis + Binance + Deriv sources)
        tick_store.append(pair, time.time(), float(candle["close"]))
        kf = kalman_filters.get(pair)
        if kf:
            kf.update(float(candle["close"]))
    await broadcast({"type": "candle_update", "pair": pair, "granularity": granularity, "candle": candle})
    if granularity == 60 and candle.get("closed", False):
        await on_1m_close(pair, candle)


async def on_1m_close(pair: str, closed_candle: dict[str, Any]) -> None:
    log.debug("1M close: %s @ %s", pair, closed_candle.get("epoch"))
    candles_1m = candle_store[pair].get(60, [])
    df_1m      = _candles_to_df(candles_1m)
    if len(df_1m) < config.MIN_CANDLES:
        return
    gate = _check_signal_gates(pair, df_1m)
    if gate:
        await broadcast({"type": "signal_blocked", "pair": pair, "reason": gate})
        return
    candle_dfs: dict[int, pd.DataFrame] = {}
    for gran in config.MTF_ANALYSIS_TFS:
        c = candle_store[pair].get(gran, [])
        if c:
            candle_dfs[gran] = _candles_to_df(c)
    utc_hour   = datetime.now(timezone.utc).hour
    utc_minute = datetime.now(timezone.utc).minute
    signal = await _run_signal_pipeline(pair, df_1m, candle_dfs, utc_hour, utc_minute)
    if signal["grade"] != "SKIP":
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
            return
        signal["window_plan"] = ws_sel.get_window_plan() if ws_sel else []
        await broadcast({"type": "signal", "pair": pair, **signal})
        tab = tabular_models.get(pair)
        if tab and tab.should_retrain(1):
            asyncio.create_task(train_models_for_pair(pair))
        asyncio.create_task(send_signal_alert(signal, pair))


async def _run_signal_pipeline(
    pair: str,
    df_1m: pd.DataFrame,
    candle_dfs: dict[int, pd.DataFrame],
    utc_hour: int,
    utc_minute: int,
) -> dict[str, Any]:
    feat = get_last_features(df_1m, utc_hour)
    adx  = float(feat.get("adx_14", 0))
    smc  = compute_smc_features(df_1m, utc_hour)
    inst = compute_institutional(df_1m, utc_hour, utc_minute)
    advanced = compute_all_advanced(df_1m)
    wyckoff       = {k: int(v) for k, v in advanced.items() if k.startswith("wyckoff")}
    elliott       = {k: float(v) for k, v in advanced.items() if k.startswith("elliott")}
    sd            = {k: float(v) for k, v in advanced.items() if k.startswith("sd_")}
    chart_patterns = {k: float(v) for k, v in advanced.items() if k.startswith("chart_")}
    price_hold = analyze_price_hold(tick_store.get(pair), window_sec=3.0)
    reaction  = compute_candle_reaction(df_1m, price_hold=price_hold)
    harmonics = detect_harmonics(df_1m)
    mtf = compute_mtf_agreement(candle_dfs, utc_hour)
    if float(mtf.get("agreement", 0)) < config.MTF_MIN_AGREEMENT:
        return _skip_signal("mtf_conflict")
    # Plan: 1H and 15M must NOT be opposite (neutral 15M is OK, only conflict = skip)
    if config.MTF_H1_15M_ALIGN and mtf.get("h1_15m_conflict", False):
        return _skip_signal("h1_15m_conflict")

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

    # Combine model probs — rule_conf inverted for RED direction
    all_model_probs: dict[str, float] = {}
    all_model_probs.update(tab_probs)  # type: ignore[arg-type]
    all_model_probs.update(deep_probs)
    if rule_dir == "GREEN":
        all_model_probs["rule"] = rule_conf
    elif rule_dir == "RED":
        all_model_probs["rule"] = 1.0 - rule_conf
    else:
        all_model_probs["rule"] = 0.5
    all_model_probs["bayes"] = float(bayes.get("bayes_mean", 0.5))

    # Accuracy-weighted fusion (skill above coin-flip as weight)
    if all_model_probs:
        weights: list[float] = []
        probs:   list[float] = []
        for name, prob in all_model_probs.items():
            if tab and name in tab.model_accuracies:
                acc = float(tab.model_accuracies[name])
            elif dp and hasattr(dp, "accuracy_map") and name in dp.accuracy_map:
                acc = float(dp.accuracy_map[name])
            else:
                acc = 0.52
            w = max(0.01, acc - 0.50)
            weights.append(w)
            probs.append(prob)
        total_w = sum(weights) or 1.0
        base_fused = sum(p * w for p, w in zip(probs, weights)) / total_w
    else:
        base_fused = 0.5

    direction = "GREEN" if base_fused > 0.5 else "RED"
    if tab_dir == "SKIP" and rule_dir == "SKIP":
        direction = "SKIP"
    if direction == "SKIP":
        return _skip_signal("no_consensus")

    # Plan: 8/13+ AI models must agree with direction, avg confidence 59%+
    # Models at 45-55% are neutral/abstain — only clearly aligned/opposing count
    if all_model_probs:
        bull_signal = direction == "GREEN"
        if bull_signal:
            aligned_probs  = [p for p in all_model_probs.values() if p > 0.55]
            opposing_probs = [p for p in all_model_probs.values() if p < 0.45]
        else:
            aligned_probs  = [p for p in all_model_probs.values() if p < 0.45]
            opposing_probs = [p for p in all_model_probs.values() if p > 0.55]
        n_agree  = len(aligned_probs)
        n_oppose = len(opposing_probs)
        avg_conf = (sum(aligned_probs) / n_agree) if n_agree else 0.0
        # Skip only if: fewer than required agree AND more opposing than aligned
        if n_agree < config.AI_MIN_MODELS_AGREE and n_oppose >= n_agree:
            return _skip_signal("ai_weak_consensus")

    stack_model = stacking_models.get(pair)
    if stack_model and stack_model.trained:
        stack_prob = stack_model.predict(list(all_model_probs.values()))
        base_fused = stack_model.fuse(base_fused, stack_prob)

    kf = kalman_filters.get(pair)
    kalman_trend_d = kf.get_trend() if kf else {}
    kt = int(kalman_trend_d.get("kalman_trend", 0))
    kv = float(kalman_trend_d.get("kalman_velocity", 0))
    kalman_agree = (kt == 1 and direction == "GREEN") or (kt == -1 and direction == "RED")

    ppo = ppo_agents.get(pair)
    if ppo:
        ppo_dir = ppo.decide(feat, base_fused, direction)
        if ppo_dir == "SKIP":
            return _skip_signal("ppo_skip")

    meta = meta_labelers.get(pair)
    meta_prob = meta.predict(feat) if meta else 0.75
    if meta_prob < 0.50:
        return _skip_signal("meta_label_block")

    mtf_pts    = mtf_score_pts(mtf, direction)
    # Plan: MTF layer must score 20+/45 (at least 2 aligned TFs)
    if mtf_pts < config.MTF_PTS_MIN:
        return _skip_signal("mtf_insufficient")
    smc_pts    = smc_score_pts(smc, inst, wyckoff, harmonics, sd, elliott, direction, chart_patterns)
    # Plan: SMC layer must score 12+/30
    if smc_pts < config.SMC_PTS_MIN:
        return _skip_signal("smc_insufficient")
    react_pts  = reaction_score_pts(reaction, direction)
    ai_pts_val = ai_score_pts(base_fused, meta_prob)

    confidence = score_to_confidence(
        mtf_pts, smc_pts, react_pts, ai_pts_val,
        meta_prob >= 0.60, adx, kalman_agree, kv,
    )
    h_boost = harmonic_confidence_boost(harmonics, direction)
    confidence = min(confidence + h_boost, 0.99)
    cons_score = consensus_check(feat, direction)
    confidence = apply_consensus_penalty(confidence, cons_score)

    # Volume-against-signal penalty: high-volume candle opposing direction = institutional counter-flow
    bull_signal = direction == "GREEN"
    if bull_signal and feat.get("vsa_high_vol_bear", 0):
        confidence *= 0.80  # big selling volume while we're long → reduce confidence
    elif not bull_signal and feat.get("vsa_high_vol_bull", 0):
        confidence *= 0.80  # big buying volume while we're short → reduce confidence
    # Volume spike bonus: high volume confirming direction = institutional backing
    elif bull_signal and feat.get("vsa_high_vol_bull", 0):
        confidence = min(confidence + 0.03, 0.99)
    elif not bull_signal and feat.get("vsa_high_vol_bear", 0):
        confidence = min(confidence + 0.03, 0.99)

    # Exhaustion penalty: chasing late entries at trend tops/bottoms
    # 5+ consecutive same-dir candles + overbought/oversold RSI = move likely exhausted
    mom_bull = int(smc.get("momentum_bull_count", 0))
    mom_bear = int(smc.get("momentum_bear_count", 0))
    rsi14 = float(feat.get("rsi_14", 50))
    if bull_signal and mom_bull >= 5 and rsi14 > 70:
        confidence *= 0.80   # bull exhaustion: don't chase the top
    elif (not bull_signal) and mom_bear >= 5 and rsi14 < 30:
        confidence *= 0.80   # bear exhaustion: don't chase the bottom

    grade_str = grade(confidence)
    if grade_str == "SKIP":
        return _skip_signal("below_threshold")
    if loss_streak.get(pair, 0) >= 5:
        return _skip_signal("circuit_breaker")

    pattern_names: list[str] = []
    if reaction.get("pin_bull"):   pattern_names.append("pin_bar_bull")
    if reaction.get("pin_bear"):   pattern_names.append("pin_bar_bear")
    if reaction.get("bull_engulf"): pattern_names.append("bullish_engulfing")
    if reaction.get("bear_engulf"): pattern_names.append("bearish_engulfing")
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
        "price_hold":       price_hold,
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
    now = time.time()
    # Forex weekend close: Friday 21:00 UTC → Sunday 22:00 UTC
    dt  = datetime.now(timezone.utc)
    dow = dt.weekday()
    h   = dt.hour
    if dow == 4 and h >= 21: return "market_closed"
    if dow in (5, 6):        return "market_closed"
    if is_news_window(pair, now):
        return "news_window"
    if len(df) >= 20:
        atr      = float((df["high"] - df["low"]).rolling(14).mean().iloc[-1])
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


# ── WebSocket ────────────────────────────────────────────────────────────

@app.websocket("/ws/{pair}")
async def websocket_endpoint(ws: WebSocket, pair: str) -> None:
    await ws.accept()
    ws_clients.add(ws)
    log.info("WS connected: %s — %s", pair, ws.client)
    try:
        await ws.send_json({"type": "market_status", "open": True, "pair": pair})
        candles = candle_store.get(pair, {}).get(60, [])
        if candles:
            await ws.send_json({"type": "history", "pair": pair, "candles": candles[-300:]})
        tab = tabular_models.get(pair)
        await ws.send_json({
            "type":       "model_status",
            "pair":       pair,
            "is_trained": bool(tab and tab.trained),
            "accuracy":   tab.accuracy if tab else 0.0,
            "n_candles":  tab.n_candles if tab else 0,
        })
        while True:
            await ws.receive_text()
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


async def news_refresher() -> None:
    while True:
        try:
            await fetch_news_events()
        except Exception:
            pass
        await asyncio.sleep(3600)


# ── REST Endpoints ──────────────────────────────────────────────────────────

@app.get("/api/pairs")
async def get_pairs() -> dict[str, Any]:
    return {"forex": config.FOREX_PAIRS, "status": "ok"}


@app.get("/api/candles/{pair}")
async def get_candles(pair: str, granularity: int = 60, count: int = 300) -> dict[str, Any]:
    candles = candle_store.get(pair, {}).get(granularity, [])
    return {"pair": pair, "granularity": granularity, "candles": candles[-count:] if count else candles}


@app.get("/api/model/{pair}")
async def get_model_status(pair: str) -> dict[str, Any]:
    tab  = tabular_models.get(pair)
    deep = deep_models.get(pair)
    return {
        "pair":    pair,
        "tabular": {"trained": bool(tab and tab.trained), "accuracy": tab.accuracy if tab else 0, "n_candles": tab.n_candles if tab else 0},
        "deep":    {"trained": bool(deep and deep.trained), "models": list(deep.accuracy_map.keys()) if deep else []},
    }


@app.post("/api/result/{pair}")
async def report_result(pair: str, won: bool) -> dict[str, Any]:
    if won:
        loss_streak[pair] = 0
    else:
        loss_streak[pair] = loss_streak.get(pair, 0) + 1
    return {"pair": pair, "streak": loss_streak.get(pair, 0)}


# ── Helpers ────────────────────────────────────────────────────────────────

def _candles_to_df(candles: list[dict[str, Any]]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=["epoch","open","high","low","close"])
    df = pd.DataFrame(candles)
    for col in ["open", "high", "low", "close"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("epoch").reset_index(drop=True)
    return df


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=config.API_HOST, port=config.API_PORT, reload=False)
