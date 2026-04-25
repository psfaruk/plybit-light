use pyo3::prelude::*;
use std::collections::HashMap;

/// Compute 7-component candle reaction scores.
/// Input: list of (open, high, low, close) tuples (most recent last).
/// Returns dict with bull_score, bear_score, net_score, signal_boost, flags.
#[pyfunction]
pub fn react(candles: Vec<(f64, f64, f64, f64)>) -> PyResult<HashMap<String, f64>> {
    let mut result = HashMap::new();
    if candles.len() < 2 {
        result.insert("bull_score".into(),  50.0);
        result.insert("bear_score".into(),  50.0);
        result.insert("net_score".into(),   0.0);
        result.insert("signal_boost".into(),0.0);
        return Ok(result);
    }

    let (o,  h,  l,  c)  = candles[candles.len() - 1];
    let (po, ph, pl, pc) = candles[candles.len() - 2];

    let rng  = (h - l).max(1e-10);
    let body = (c - o).abs();

    // ATR from last 14 candles
    let atr_len = candles.len().min(14);
    let atr: f64 = candles[candles.len() - atr_len ..]
        .iter()
        .map(|(_, ch, cl, _)| ch - cl)
        .sum::<f64>() / atr_len as f64;

    let close_pos = (c - l) / rng;
    let lower_wick = o.min(c) - l;
    let upper_wick = h - o.max(c);

    // ── 1. Wick rejection ─────────────────────────────────
    let bull_wick = (lower_wick / rng) * 100.0;
    let bear_wick = (upper_wick / rng) * 100.0;

    // ── 2. Pin bar ────────────────────────────────────────
    let pin_bull = lower_wick >= body * 2.0 && body <= rng * 0.25;
    let pin_bear = upper_wick >= body * 2.0 && body <= rng * 0.25;
    let pin_bull_score = if pin_bull { 85.0 } else { lower_wick / rng * 60.0 };
    let pin_bear_score = if pin_bear { 85.0 } else { upper_wick / rng * 60.0 };

    // ── 3. Body position ──────────────────────────────────
    let bull_body = close_pos * 100.0;
    let bear_body = (1.0 - close_pos) * 100.0;

    // ── 4. Momentum ───────────────────────────────────────
    let bull_mom_score = if c > pc && c > o { 75.0 } else { 20.0 };
    let bear_mom_score = if c < pc && c < o { 75.0 } else { 20.0 };

    // ── 5. ATR relative size ──────────────────────────────
    let atr_score = ((rng / atr.max(1e-10)) * 70.0).min(100.0);

    // ── 6. Engulfing ──────────────────────────────────────
    let prev_body = (pc - po).abs();
    let bull_engulf = c > o && c > ph && o < pl && body > prev_body;
    let bear_engulf = c < o && c < pl && o > ph && body > prev_body;
    let bull_engulf_score = if bull_engulf { 95.0 } else { 0.0 };
    let bear_engulf_score = if bear_engulf { 95.0 } else { 0.0 };

    // ── Weighted composite ────────────────────────────────
    let weights = [0.20_f64, 0.18, 0.18, 0.15, 0.10, 0.14, 0.05];
    let bull_components = [bull_wick, pin_bull_score, bull_body, bull_mom_score, atr_score, bull_engulf_score, 0.0];
    let bear_components = [bear_wick, pin_bear_score, bear_body, bear_mom_score, atr_score, bear_engulf_score, 0.0];

    let bull_score: f64 = weights.iter().zip(bull_components.iter()).map(|(w, s)| w * s).sum();
    let bear_score: f64 = weights.iter().zip(bear_components.iter()).map(|(w, s)| w * s).sum();
    let net_score = bull_score - bear_score;

    result.insert("bull_score".into(),   bull_score);
    result.insert("bear_score".into(),   bear_score);
    result.insert("net_score".into(),    net_score);
    result.insert("signal_boost".into(), (net_score / 100.0) * 0.12);
    result.insert("pin_bull".into(),     pin_bull as i32 as f64);
    result.insert("pin_bear".into(),     pin_bear as i32 as f64);
    result.insert("pin_bar".into(),      (pin_bull || pin_bear) as i32 as f64);
    result.insert("bull_engulf".into(),  bull_engulf as i32 as f64);
    result.insert("bear_engulf".into(),  bear_engulf as i32 as f64);
    result.insert("engulfing".into(),    (bull_engulf || bear_engulf) as i32 as f64);
    result.insert("close_pos".into(),    close_pos);
    result.insert("atr_score".into(),    atr_score);

    Ok(result)
}
