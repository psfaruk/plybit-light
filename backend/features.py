"""100+ technical feature engineering for PLAYBIT AI."""

import numpy as np
import pandas as pd
from typing import Optional


def _ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def _sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n).mean()


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(n).mean()
    loss  = (-delta.clip(upper=0)).rolling(n).mean()
    rs    = gain / (loss + 1e-10)
    return 100 - (100 / (1 + rs))


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def _macd(close: pd.Series):
    fast   = _ema(close, 12)
    slow   = _ema(close, 26)
    macd   = fast - slow
    signal = _ema(macd, 9)
    hist   = macd - signal
    return macd, signal, hist


def _stoch(high: pd.Series, low: pd.Series, close: pd.Series, k: int = 14, d: int = 3):
    lowest  = low.rolling(k).min()
    highest = high.rolling(k).max()
    stoch_k = 100 * (close - lowest) / (highest - lowest + 1e-10)
    stoch_d = stoch_k.rolling(d).mean()
    return stoch_k, stoch_d


def _bb(close: pd.Series, n: int = 20, std: float = 2.0):
    sma    = _sma(close, n)
    sigma  = close.rolling(n).std()
    upper  = sma + std * sigma
    lower  = sma - std * sigma
    return upper, sma, lower


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14):
    plus_dm  = (high.diff()).clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    mask = plus_dm < minus_dm
    plus_dm[mask]  = 0
    mask2 = minus_dm < plus_dm
    minus_dm[mask2] = 0

    atr_     = _atr(high, low, close, n)
    plus_di  = 100 * plus_dm.rolling(n).mean()  / (atr_ + 1e-10)
    minus_di = 100 * minus_dm.rolling(n).mean() / (atr_ + 1e-10)
    dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    adx      = dx.rolling(n).mean()
    return adx, plus_di, minus_di


def compute_features(df: pd.DataFrame, utc_hour: int = 0, session_data: Optional[dict] = None) -> pd.DataFrame:
    """
    Compute 100+ features from OHLCV candle dataframe.
    df columns: open, high, low, close, volume (optional)
    Returns feature dataframe (same index as df).
    """
    feat = pd.DataFrame(index=df.index)
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    vol = df.get("volume", pd.Series(np.ones(len(df)), index=df.index))

    rng = h - l + 1e-10
    body = (c - o).abs()

    # ── Candle Structure ─────────────────────────────────────
    feat["body_pct"]         = body / rng
    feat["body_ratio"]       = body / (rng)
    feat["upper_wick_ratio"] = (h - c.clip(lower=o)) / rng
    feat["lower_wick_ratio"] = (c.clip(upper=o) - l) / rng
    for i in range(1, 5):
        feat[f"prev_{i}_green"] = (c.shift(i) > o.shift(i)).astype(int)
    feat["consec_green"] = feat[["prev_1_green","prev_2_green","prev_3_green"]].sum(axis=1)
    feat["consec_red"]   = 3 - feat["consec_green"]

    # ── Moving Averages ──────────────────────────────────────
    for n in [5, 10, 20, 50, 200]:
        feat[f"ema_{n}"] = _ema(c, n)
    for n in [5, 10, 20]:
        feat[f"sma_{n}"] = _sma(c, n)

    feat["c_vs_ema5"]    = (c - feat["ema_5"])  / (c + 1e-10)
    feat["c_vs_ema20"]   = (c - feat["ema_20"]) / (c + 1e-10)
    feat["c_vs_sma20"]   = (c - feat["sma_20"]) / (c + 1e-10)
    feat["ema5_vs_ema20"]  = (feat["ema_5"]  - feat["ema_20"]) / (c + 1e-10)
    feat["ema10_vs_ema50"] = (feat["ema_10"] - feat["ema_50"]) / (c + 1e-10)
    feat["ema5_slope"]   = feat["ema_5"].diff(3)  / (c + 1e-10)
    feat["ema20_slope"]  = feat["ema_20"].diff(5) / (c + 1e-10)

    # ── Momentum ─────────────────────────────────────────────
    for n in [7, 14, 21]:
        feat[f"rsi_{n}"] = _rsi(c, n)
    feat["rsi_slope"] = feat["rsi_14"].diff(3)

    macd, macd_sig, macd_hist = _macd(c)
    feat["macd"]           = macd
    feat["macd_signal"]    = macd_sig
    feat["macd_hist"]      = macd_hist
    feat["macd_hist_slope"]= macd_hist.diff(2)

    stoch_k, stoch_d = _stoch(h, l, c)
    feat["stoch_k"]    = stoch_k
    feat["stoch_d"]    = stoch_d
    feat["stoch_slope"]= stoch_k.diff(2)

    feat["williams_r"] = -100 * (h.rolling(14).max() - c) / (h.rolling(14).max() - l.rolling(14).min() + 1e-10)
    feat["cci_20"]     = (c - _sma(c, 20)) / (0.015 * c.rolling(20).std() + 1e-10)

    for n in [3, 5, 10]:
        feat[f"roc_{n}"] = c.pct_change(n) * 100

    # ── Volatility ───────────────────────────────────────────
    atr14 = _atr(h, l, c, 14)
    feat["atr_14"]  = atr14
    feat["atr_pct"] = atr14 / (c + 1e-10)

    bb_u, bb_m, bb_l = _bb(c, 20, 2.0)
    feat["bb_upper"]   = bb_u
    feat["bb_lower"]   = bb_l
    feat["bb_middle"]  = bb_m
    feat["bb_width"]   = (bb_u - bb_l) / (bb_m + 1e-10)
    feat["bb_pos"]     = (c - bb_l) / (bb_u - bb_l + 1e-10)
    feat["bb_squeeze"] = (feat["bb_width"] < feat["bb_width"].rolling(20).mean() * 0.8).astype(int)

    kelt_basis = _ema(c, 20)
    kelt_range = _atr(h, l, c, 10) * 2
    feat["keltner_upper"] = kelt_basis + kelt_range
    feat["keltner_lower"] = kelt_basis - kelt_range

    # ── Trend Strength ───────────────────────────────────────
    adx, plus_di, minus_di = _adx(h, l, c)
    feat["adx_14"]   = adx
    feat["plus_di"]  = plus_di
    feat["minus_di"] = minus_di
    feat["di_diff"]  = plus_di - minus_di

    for n in [5, 10, 20]:
        feat[f"dir_{n}bar"] = (c > c.shift(n)).astype(int)

    # ── Volume / VSA ─────────────────────────────────────────
    vol_avg = vol.rolling(20).mean() + 1e-10
    feat["volume_ratio"]      = vol / vol_avg
    feat["volume_spike"]      = (vol > vol_avg * 2).astype(int)
    feat["obv"]               = (np.sign(c.diff()) * vol).cumsum()
    feat["vwap"]              = (c * vol).rolling(20).sum() / (vol.rolling(20).sum() + 1e-10)

    green_bar = (c > o).astype(float)
    red_bar   = (c < o).astype(float)
    feat["vsa_high_vol_bull"] = ((vol > vol_avg * 1.5) & (c > o)).astype(int)
    feat["vsa_high_vol_bear"] = ((vol > vol_avg * 1.5) & (c < o)).astype(int)
    feat["vsa_low_vol_up"]    = ((vol < vol_avg * 0.7) & (c > o)).astype(int)
    feat["vsa_low_vol_down"]  = ((vol < vol_avg * 0.7) & (c < o)).astype(int)
    feat["vsa_spread_vs_vol"] = rng / (vol + 1e-10)
    close_pos = (c - l) / rng
    feat["delta_approx"] = (close_pos - 0.5) * vol

    # ── Session ──────────────────────────────────────────────
    feat["utc_hour"]       = utc_hour
    feat["london_session"] = int(7 <= utc_hour < 16)
    feat["ny_session"]     = int(12 <= utc_hour < 21)
    feat["asian_session"]  = int(0 <= utc_hour < 8)
    feat["overlap_session"]= int(12 <= utc_hour < 16)
    feat["day_of_week"]    = 0  # populated externally

    # ── Market Regime ────────────────────────────────────────
    feat["regime_trend"]    = (adx > 25).astype(int)
    feat["regime_range"]    = (adx < 20).astype(int)
    feat["regime_volatile"] = (atr14 > atr14.rolling(20).mean() * 1.5).astype(int)
    feat["atr_regime"]      = atr14 / (atr14.rolling(50).mean() + 1e-10)

    feat = feat.fillna(0.0)
    return feat


def get_last_features(df: pd.DataFrame, utc_hour: int = 0) -> dict:
    """Get features for the most recent candle as a flat dict."""
    if len(df) < 30:
        return {}
    feat_df = compute_features(df, utc_hour)
    last = feat_df.iloc[-1].to_dict()
    # tag with htf prefix for multi-TF use
    return last
