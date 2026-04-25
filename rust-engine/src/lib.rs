use pyo3::prelude::*;

mod candle_reaction;
mod pattern_detector;
mod wick_analyzer;
mod zone_detector;

/// Python bindings for the PLAYBIT Rust engine.
#[pymodule]
fn playbit_engine(_py: Python<'_>, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(candle_reaction::react, m)?)?;
    m.add_function(wrap_pyfunction!(pattern_detector::detect_patterns, m)?)?;
    m.add_function(wrap_pyfunction!(wick_analyzer::analyze_wick, m)?)?;
    m.add_function(wrap_pyfunction!(zone_detector::detect_zones, m)?)?;
    Ok(())
}
