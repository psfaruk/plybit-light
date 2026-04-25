"""Model 14: Stacking Ensemble — meta-learner on top of Models 1-13."""

import os
import numpy as np
import logging
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
import joblib

log = logging.getLogger(__name__)

MODELS_DIR  = os.path.join(os.path.dirname(__file__), "models")
STACK_PATH  = os.path.join(MODELS_DIR, "stacking.joblib")


class StackingEnsemble:
    """
    Level-2 meta-learner.
    Input: probability outputs from all level-0 models.
    Output: calibrated final probability.
    """

    def __init__(self) -> None:
        self.meta: LogisticRegression | None = None
        self.scaler = StandardScaler()
        self.trained = False
        self._load()

    def train(self, model_probs_matrix: np.ndarray, y: np.ndarray) -> float:
        """
        model_probs_matrix: shape (n_samples, n_models)
        y: true labels (0/1)
        """
        if len(model_probs_matrix) < 50:
            return 0.5

        X_scaled = self.scaler.fit_transform(model_probs_matrix)
        self.meta = LogisticRegression(C=1.0, max_iter=500, random_state=42)
        self.meta.fit(X_scaled, y)
        preds     = self.meta.predict(X_scaled)
        acc       = float(np.mean(preds == y))
        self.trained = True
        self._save()
        log.info("Stacking ensemble trained, acc=%.3f", acc)
        return acc

    def predict(self, model_probs: list[float]) -> float:
        """Returns final stacked probability."""
        if self.meta is None or not model_probs:
            return float(np.mean(model_probs)) if model_probs else 0.5

        X = np.array(model_probs, dtype=np.float32).reshape(1, -1)
        try:
            X_sc = self.scaler.transform(X)
            prob = float(self.meta.predict_proba(X_sc)[0][1])
        except Exception:
            prob = float(np.mean(model_probs))
        return prob

    def fuse(self, base_prob: float, stack_prob: float) -> float:
        """Combine base ensemble (60%) with stacking (40%)."""
        return base_prob * 0.60 + stack_prob * 0.40

    def _save(self) -> None:
        os.makedirs(MODELS_DIR, exist_ok=True)
        joblib.dump({"meta": self.meta, "scaler": self.scaler}, STACK_PATH)

    def _load(self) -> None:
        if not os.path.exists(STACK_PATH):
            return
        try:
            data       = joblib.load(STACK_PATH)
            self.meta  = data["meta"]
            self.scaler= data["scaler"]
            self.trained = True
        except Exception as e:
            log.warning("Stacking load error: %s", e)
