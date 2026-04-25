use pyo3::prelude::*;
use std::collections::HashMap;

/// Detect OB/FVG zone reactions for a price level.
#[pyfunction]
pub fn detect_zones(
    price: f64,
    ob_zones:  Vec<(f64, f64)>,  // list of (low, high) for OBs
    fvg_zones: Vec<(f64, f64)>,  // list of (low, high) for FVGs
) -> PyResult<HashMap<String, f64>> {
    let mut result = HashMap::new();

    let in_ob  = ob_zones.iter().any(|(lo, hi)| price >= *lo && price <= *hi);
    let in_fvg = fvg_zones.iter().any(|(lo, hi)| price >= *lo && price <= *hi);

    // Distance to nearest zone
    let nearest_ob_dist = ob_zones.iter()
        .map(|(lo, hi)| {
            let mid = (lo + hi) / 2.0;
            (price - mid).abs() / mid.max(1e-10)
        })
        .fold(f64::INFINITY, f64::min);

    let nearest_fvg_dist = fvg_zones.iter()
        .map(|(lo, hi)| {
            let mid = (lo + hi) / 2.0;
            (price - mid).abs() / mid.max(1e-10)
        })
        .fold(f64::INFINITY, f64::min);

    result.insert("in_ob".into(),              if in_ob  { 1.0 } else { 0.0 });
    result.insert("in_fvg".into(),             if in_fvg { 1.0 } else { 0.0 });
    result.insert("zone_reaction".into(),      if in_ob || in_fvg { 1.0 } else { 0.0 });
    result.insert("nearest_ob_dist".into(),    nearest_ob_dist);
    result.insert("nearest_fvg_dist".into(),   nearest_fvg_dist);
    result.insert("zone_score".into(),
        if in_ob || in_fvg { 90.0 }
        else if nearest_ob_dist < 0.01 || nearest_fvg_dist < 0.01 { 60.0 }
        else { 0.0 }
    );

    Ok(result)
}
