"""Meta-labeling filter (de Prado) — eliminates false signals from primary model."""

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
import joblib
import os
import logging

log = logging.getLogger(__name__)

_MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
_RETRAIN_THRESHOLD = 80   # retrain after this many labeled samples


class MetaLabeler:
    def __init__(self, pair: str = "global") -> None:
        self.pair        = pair
        self._model_path  = os.path.join(_MODELS_DIR, f"meta_label_{pair}.joblib")
        self._scaler_path = os.path.join(_MODELS_DIR, f"meta_scaler_{pair}.joblib")
        self.model: RandomForestClassifier | None = None
        self.scaler: StandardScaler | None = None
        # Rolling buffer for online retraining: list of (feat_array, label)
        self._train_buf: list[tuple[np.ndarray, int]] = []
        # Last predicted feature vector (for pairing with next outcome)
        self._last_feat: np.ndarray | None = None
        self._load()

    def _load(self) -> None:
        if os.path.exists(self._model_path) and os.path.exists(self._scaler_path):
            try:
                self.model  = joblib.load(self._model_path)
                self.scaler = joblib.load(self._scaler_path)
            except Exception as e:
                log.warning("MetaLabeler[%s] load error: %s", self.pair, e)

    def predict(self, features: dict[str, float]) -> float:
        """Returns probability [0,1] that the primary signal is correct."""
        arr = _dict_to_array(features)
        self._last_feat = arr  # remember for record_outcome pairing

        if self.model is None:
            return 0.75  # default pass-through when not trained

        if self.scaler is not None:
            arr = self.scaler.transform(arr)
        return float(self.model.predict_proba(arr)[0][1])

    def record_outcome(self, confidence: float, is_win: bool) -> None:
        """Called after a trade settles. Buffers the last prediction for retraining."""
        if self._last_feat is None:
            return
        label = 1 if is_win else 0
        self._train_buf.append((self._last_feat.copy(), label))
        self._last_feat = None
        # Keep buffer bounded
        if len(self._train_buf) > 2000:
            self._train_buf = self._train_buf[-2000:]
        # Retrain periodically
        if len(self._train_buf) >= _RETRAIN_THRESHOLD and len(self._train_buf) % 20 == 0:
            self._retrain_from_buffer()

    def _retrain_from_buffer(self) -> None:
        if len(self._train_buf) < _RETRAIN_THRESHOLD:
            return
        X = np.vstack([f for f, _ in self._train_buf])
        y = np.array([l for _, l in self._train_buf], dtype=np.int32)
        try:
            self.train(X, y)
            log.info("MetaLabeler[%s] retrained on %d samples", self.pair, len(y))
        except Exception as e:
            log.warning("MetaLabeler[%s] retrain error: %s", self.pair, e)

    def train(self, X: np.ndarray, y: np.ndarray) -> None:
        self.scaler = StandardScaler()
        X_scaled    = self.scaler.fit_transform(X)
        self.model  = RandomForestClassifier(
            n_estimators=200, max_depth=6,
            min_samples_leaf=5, class_weight="balanced",
            random_state=42,
        )
        self.model.fit(X_scaled, y)
        os.makedirs(_MODELS_DIR, exist_ok=True)
        joblib.dump(self.model,  self._model_path)
        joblib.dump(self.scaler, self._scaler_path)

    def is_trained(self) -> bool:
        return self.model is not None


def _dict_to_array(d: dict[str, float]) -> np.ndarray:
    vals = list(d.values())
    return np.array(vals, dtype=np.float32).reshape(1, -1)
