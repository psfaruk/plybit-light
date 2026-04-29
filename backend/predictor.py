"""
Ensemble predictor — Models 1-6 (tabular) + Meta-ensemble orchestration.
Models 7-13 (deep) handled in deep_predictor.py.
"""

import os
import logging
import numpy as np
import pandas as pd
from sklearn.ensemble import (
    RandomForestClassifier,
    ExtraTreesClassifier,
    HistGradientBoostingClassifier,
)
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler
from sklearn.calibration import CalibratedClassifierCV
import xgboost as xgb
import lightgbm as lgb
import catboost as cb
import joblib

from features import compute_features, get_last_features
from config import MIN_CANDLES, GOOD_CANDLES, BEST_CANDLES, RETRAIN_EVERY

log = logging.getLogger(__name__)

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
os.makedirs(MODELS_DIR, exist_ok=True)


def _label(df: pd.DataFrame, lookahead: int = 1) -> pd.Series:
    """1 = next candle closes higher (GREEN), 0 = RED."""
    return (df["close"].shift(-lookahead) > df["close"]).astype(int)


def _prep_xy(df: pd.DataFrame, utc_hour: int = 0) -> tuple[np.ndarray, np.ndarray]:
    feat_df = compute_features(df, utc_hour)
    labels  = _label(df)
    # Drop last row (no label) and NaN rows
    feat_df = feat_df.iloc[:-1]
    labels  = labels.iloc[:-1]
    mask    = feat_df.notna().all(axis=1)
    X = feat_df[mask].values.astype(np.float32)
    y = labels[mask].values.astype(np.int32)
    return X, y


class TabularPredictor:
    """Models 1-6: XGBoost, LightGBM, CatBoost, RandomForest, ExtraTrees, HistGB."""

    MODEL_NAMES = ["xgb", "lgbm", "catboost", "rf", "extra", "histgb"]

    def __init__(self, pair: str, granularity: int = 60) -> None:
        self.pair        = pair
        self.granularity = granularity
        self.scaler      = StandardScaler()
        self.models: dict[str, object] = {}
        self.model_accuracies: dict[str, float] = {}  # per-model CV mean
        self.n_candles   = 0
        self.accuracy    = 0.0
        self.trained     = False
        self._candles_since_retrain = 0
        self._load()

    # ── Training ─────────────────────────────────────────────

    def train(self, df: pd.DataFrame, utc_hour: int = 0) -> dict[str, float]:
        if len(df) < MIN_CANDLES:
            return {}

        X, y = _prep_xy(df, utc_hour)
        if len(X) < MIN_CANDLES:
            return {}

        X_scaled = self.scaler.fit_transform(X)

        results: dict[str, float] = {}

        # 1. XGBoost
        self.models["xgb"] = xgb.XGBClassifier(
            n_estimators=400, max_depth=5, learning_rate=0.03,
            subsample=0.8, colsample_bytree=0.8,
            use_label_encoder=False, eval_metric="logloss",
            tree_method="hist", random_state=42,
        )
        # 2. LightGBM
        self.models["lgbm"] = lgb.LGBMClassifier(
            n_estimators=400, num_leaves=31, learning_rate=0.03,
            subsample=0.8, random_state=42, verbose=-1,
        )
        # 3. CatBoost
        self.models["catboost"] = cb.CatBoostClassifier(
            iterations=400, depth=5, learning_rate=0.03,
            verbose=0, random_seed=42,
        )
        # 4. Random Forest
        self.models["rf"] = RandomForestClassifier(
            n_estimators=300, max_depth=8, min_samples_leaf=5,
            class_weight="balanced", random_state=42, n_jobs=-1,
        )
        # 5. ExtraTrees
        self.models["extra"] = ExtraTreesClassifier(
            n_estimators=300, max_depth=8, min_samples_leaf=5,
            class_weight="balanced", random_state=42, n_jobs=-1,
        )
        # 6. HistGradientBoosting
        self.models["histgb"] = HistGradientBoostingClassifier(
            max_iter=300, max_depth=5, learning_rate=0.02,
            l2_regularization=1.0, random_state=42,
        )

        tscv = TimeSeriesSplit(n_splits=5)
        cv_scores: dict[str, list[float]] = {k: [] for k in self.MODEL_NAMES}

        for train_idx, val_idx in tscv.split(X_scaled):
            Xtr, Xv = X_scaled[train_idx], X_scaled[val_idx]
            ytr, yv = y[train_idx], y[val_idx]
            for name in self.MODEL_NAMES:
                try:
                    m = self.models[name]
                    if hasattr(m, "fit"):
                        m.fit(Xtr, ytr)  # type: ignore[union-attr]
                        pred = m.predict(Xv)  # type: ignore[union-attr]
                        acc  = float(np.mean(pred == yv))
                        cv_scores[name].append(acc)
                except Exception as e:
                    log.warning("Model %s CV error: %s", name, e)

        # Final fit on all data + isotonic calibration
        # (plan upgrade #1: well-calibrated probabilities → confidence is reliable)
        for name in self.MODEL_NAMES:
            try:
                base = self.models[name]
                base.fit(X_scaled, y)  # type: ignore[union-attr]
                # Wrap with isotonic calibration when we have enough rows
                if len(X_scaled) >= 200:
                    try:
                        cal = CalibratedClassifierCV(base, method="isotonic", cv="prefit")  # type: ignore[arg-type]
                        cal.fit(X_scaled, y)
                        self.models[name] = cal
                    except Exception as ce:
                        log.warning("Calibration failed for %s: %s — using uncalibrated", name, ce)
                scores = cv_scores.get(name, [0.5])
                results[name] = float(np.mean(scores)) if scores else 0.5
            except Exception as e:
                log.error("Model %s train error: %s", name, e)

        self.model_accuracies = dict(results)
        self.n_candles  = len(df)
        self.accuracy   = float(np.mean(list(results.values()))) if results else 0.0
        self.trained    = True
        self._candles_since_retrain = 0

        self._save()
        log.info("Tabular models trained for %s, acc=%.3f, n=%d", self.pair, self.accuracy, self.n_candles)
        return results

    def predict(self, df: pd.DataFrame, utc_hour: int = 0) -> dict[str, object]:
        """Returns per-model confidence + fused ensemble confidence."""
        if not self.trained or len(df) < 10:
            return {"fused": 0.5, "direction": "SKIP", "model_probs": {}}

        feat = get_last_features(df, utc_hour)
        if not feat:
            return {"fused": 0.5, "direction": "SKIP", "model_probs": {}}

        X = np.array(list(feat.values()), dtype=np.float32).reshape(1, -1)
        # Handle scaler mismatch gracefully
        try:
            X_scaled = self.scaler.transform(X)
        except Exception:
            return {"fused": 0.5, "direction": "SKIP", "model_probs": {}}

        model_probs: dict[str, float] = {}
        for name in self.MODEL_NAMES:
            m = self.models.get(name)
            if m is None:
                continue
            try:
                prob = float(m.predict_proba(X_scaled)[0][1])  # type: ignore[union-attr]
                model_probs[name] = prob
            except Exception:
                pass

        if not model_probs:
            return {"fused": 0.5, "direction": "SKIP", "model_probs": {}}

        # Plan §16 fusion: accuracy-weighted average. Each model's vote weighed
        # by its CV accuracy minus 0.5 (skill above coin-flip), clamped at 0.05
        # so even a barely-skilled model contributes a tiny bit.
        weights: list[float] = []
        probs:   list[float] = []
        for name, p in model_probs.items():
            acc = float(self.model_accuracies.get(name, 0.55))
            w   = max(0.05, acc - 0.5)
            weights.append(w)
            probs.append(p)
        total_w = sum(weights) or 1.0
        fused   = float(sum(p * w for p, w in zip(probs, weights)) / total_w)
        direction = "GREEN" if fused > 0.5 else "RED"

        return {
            "fused":       fused,
            "direction":   direction,
            "model_probs": model_probs,
        }

    def should_retrain(self, new_candles: int) -> bool:
        self._candles_since_retrain += new_candles
        return self._candles_since_retrain >= RETRAIN_EVERY

    # ── Persistence ──────────────────────────────────────────

    def _save(self) -> None:
        try:
            joblib.dump({
                "models":  self.models,
                "scaler":  self.scaler,
                "n":       self.n_candles,
                "acc":     self.accuracy,
                "accs":    self.model_accuracies,
            }, self._path())
        except Exception as e:
            log.error("Save error: %s", e)

    def _load(self) -> None:
        p = self._path()
        if not os.path.exists(p):
            return
        try:
            data                  = joblib.load(p)
            self.models           = data["models"]
            self.scaler           = data["scaler"]
            self.n_candles        = data["n"]
            self.accuracy         = data["acc"]
            self.model_accuracies = data.get("accs", {})
            self.trained          = True
        except Exception as e:
            log.warning("Load error for %s: %s", self.pair, e)

    def _path(self) -> str:
        safe = self.pair.replace("/", "_")
        return os.path.join(MODELS_DIR, f"tabular_{safe}_{self.granularity}.joblib")
