use pyo3::prelude::*;
use std::collections::HashMap;

/// Detect 18 candlestick patterns from OHLC data.
/// Input: list of (open, high, low, close) tuples (most recent last, min 3 required).
/// Returns HashMap<pattern_name, 1.0 if detected>.
#[pyfunction]
pub fn detect_patterns(candles: Vec<(f64, f64, f64, f64)>) -> PyResult<HashMap<String, f64>> {
    let mut result: HashMap<String, f64> = HashMap::new();
    if candles.len() < 3 {
        return Ok(result);
    }

    let n   = candles.len();
    let c0  = candles[n - 1]; // current
    let c1  = candles[n - 2]; // previous
    let c2  = candles[n - 3]; // two back

    let (o0, h0, l0, c0v) = c0;
    let (o1, h1, l1, c1v) = c1;
    let (o2, h2, l2, c2v) = c2;

    let rng0  = (h0 - l0).max(1e-10);
    let body0 = (c0v - o0).abs();
    let rng1  = (h1 - l1).max(1e-10);
    let body1 = (c1v - o1).abs();

    let lower_wick0 = o0.min(c0v) - l0;
    let upper_wick0 = h0 - o0.max(c0v);

    // ATR approximation
    let atr: f64 = candles.iter().rev().take(14)
        .map(|(_, h, l, _)| h - l)
        .sum::<f64>() / 14.0_f64.min(candles.len() as f64);

    // ── Bullish patterns ─────────────────────────────────────
    // Hammer
    let hammer = lower_wick0 >= body0 * 2.0 && body0 <= rng0 * 0.3
        && upper_wick0 <= body0 * 0.5 && c0v > o0;
    result.insert("hammer".into(), hammer as i32 as f64);

    // Inverted hammer
    let inv_hammer = upper_wick0 >= body0 * 2.0 && body0 <= rng0 * 0.3
        && lower_wick0 <= body0 * 0.5 && c1v < o1; // downtrend
    result.insert("inverted_hammer".into(), inv_hammer as i32 as f64);

    // Bullish engulfing
    let bull_engulf = c0v > o0 && c1v < o1 && o0 <= c1v && c0v >= o1;
    result.insert("bullish_engulfing".into(), bull_engulf as i32 as f64);

    // Bullish harami
    let bull_harami = c1v < o1 && c0v > o0
        && o0 > c1v && c0v < o1 && body0 < body1 * 0.5;
    result.insert("bullish_harami".into(), bull_harami as i32 as f64);

    // Morning star
    let morning_star = c2v < o2   // bear candle
        && body1 < atr * 0.3       // doji middle
        && c0v > o0                // bull close
        && c0v > (o2 + c2v) / 2.0; // closes above midpoint of first candle
    result.insert("morning_star".into(), morning_star as i32 as f64);

    // Dragonfly doji
    let dragonfly = body0 < rng0 * 0.1 && lower_wick0 > rng0 * 0.6;
    result.insert("dragonfly_doji".into(), dragonfly as i32 as f64);

    // Tweezer bottom
    let tweezer_bot = (l0 - l1).abs() < atr * 0.05 && c0v > o0 && c1v < o1;
    result.insert("tweezer_bottom".into(), tweezer_bot as i32 as f64);

    // Three white soldiers
    let three_soldiers = n >= 3
        && c0v > o0 && c1v > o1 && c2v > o2
        && c0v > c1v && c1v > c2v
        && body0 > atr * 0.5 && body1 > atr * 0.5;
    result.insert("three_white_soldiers".into(), three_soldiers as i32 as f64);

    // Marubozu bull
    let maru_bull = c0v > o0 && body0 > atr * 0.8
        && lower_wick0 < body0 * 0.05 && upper_wick0 < body0 * 0.05;
    result.insert("marubozu_bull".into(), maru_bull as i32 as f64);

    // ── Bearish patterns ─────────────────────────────────────
    // Shooting star
    let shooting_star = upper_wick0 >= body0 * 2.0 && body0 <= rng0 * 0.3
        && lower_wick0 <= body0 * 0.5 && c0v < o0;
    result.insert("shooting_star".into(), shooting_star as i32 as f64);

    // Hanging man (hammer shape in uptrend)
    let hanging_man = lower_wick0 >= body0 * 2.0 && body0 <= rng0 * 0.3
        && c0v < o0; // bearish close
    result.insert("hanging_man".into(), hanging_man as i32 as f64);

    // Bearish engulfing
    let bear_engulf = c0v < o0 && c1v > o1 && o0 >= c1v && c0v <= o1;
    result.insert("bearish_engulfing".into(), bear_engulf as i32 as f64);

    // Bearish harami
    let bear_harami = c1v > o1 && c0v < o0
        && o0 < c1v && c0v > o1 && body0 < body1 * 0.5;
    result.insert("bearish_harami".into(), bear_harami as i32 as f64);

    // Evening star
    let evening_star = c2v > o2
        && body1 < atr * 0.3
        && c0v < o0
        && c0v < (o2 + c2v) / 2.0;
    result.insert("evening_star".into(), evening_star as i32 as f64);

    // Gravestone doji
    let gravestone = body0 < rng0 * 0.1 && upper_wick0 > rng0 * 0.6;
    result.insert("gravestone_doji".into(), gravestone as i32 as f64);

    // Tweezer top
    let tweezer_top = (h0 - h1).abs() < atr * 0.05 && c0v < o0 && c1v > o1;
    result.insert("tweezer_top".into(), tweezer_top as i32 as f64);

    // Three black crows
    let three_crows = n >= 3
        && c0v < o0 && c1v < o1 && c2v < o2
        && c0v < c1v && c1v < c2v
        && body0 > atr * 0.5 && body1 > atr * 0.5;
    result.insert("three_black_crows".into(), three_crows as i32 as f64);

    // Marubozu bear
    let maru_bear = c0v < o0 && body0 > atr * 0.8
        && lower_wick0 < body0 * 0.05 && upper_wick0 < body0 * 0.05;
    result.insert("marubozu_bear".into(), maru_bear as i32 as f64);

    Ok(result)
}
