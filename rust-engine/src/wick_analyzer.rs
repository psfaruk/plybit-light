use pyo3::prelude::*;
use std::collections::HashMap;

/// Analyze wick geometry for a single candle.
#[pyfunction]
pub fn analyze_wick(open: f64, high: f64, low: f64, close: f64) -> PyResult<HashMap<String, f64>> {
    let mut result = HashMap::new();
    let rng        = (high - low).max(1e-10);
    let body       = (close - open).abs();
    let lower_wick = open.min(close) - low;
    let upper_wick = high - open.max(close);

    result.insert("body_pct".into(),         body / rng);
    result.insert("lower_wick_pct".into(),   lower_wick / rng);
    result.insert("upper_wick_pct".into(),   upper_wick / rng);
    result.insert("body_to_lower_wick".into(), body / lower_wick.max(1e-10));
    result.insert("body_to_upper_wick".into(), body / upper_wick.max(1e-10));
    result.insert("close_pos".into(),        (close - low) / rng);
    result.insert("is_bull".into(),          if close > open { 1.0 } else { 0.0 });
    result.insert("is_doji".into(),          if body < rng * 0.05 { 1.0 } else { 0.0 });
    result.insert("is_pin_bull".into(),
        if lower_wick >= body * 2.0 && body <= rng * 0.25 { 1.0 } else { 0.0 });
    result.insert("is_pin_bear".into(),
        if upper_wick >= body * 2.0 && body <= rng * 0.25 { 1.0 } else { 0.0 });

    Ok(result)
}
