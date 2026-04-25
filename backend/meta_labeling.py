"""Meta-labeling filter (de Prado) — eliminates false signals from primary model."""

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
import joblib
import os


MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "meta_label.joblib")
SCALER_PATH = os.path.join(os.path.dirname(__file__), "models", "meta_scaler.joblib")


class MetaLabeler:
    def __init__(self) -> None:
        self.model: RandomForestClassifier | None = None
        self.scaler: StandardScaler | None = None
        self._load()

    def _load(self) -> None:
        if os.path.exists(MODEL_PATH) and os.path.exists(SCALER_PATH):
            self.model  = joblib.load(MODEL_PATH)
            self.scaler = joblib.load(SCALER_PATH)

    def predict(self, features: dict[str, float]) -> float:
        """Returns probability [0,1] that the primary signal is correct."""
        if self.model is None:
            return 0.75  # default pass-through when not trained

        X = _dict_to_array(features)
        if self.scaler is not None:
            X = self.scaler.transform(X)
        prob = float(self.model.predict_proba(X)[0][1])
        return prob

    def train(self, X: np.ndarray, y: np.ndarray) -> None:
        self.scaler = StandardScaler()
        X_scaled    = self.scaler.fit_transform(X)
        self.model  = RandomForestClassifier(
            n_estimators=200, max_depth=6,
            min_samples_leaf=5, class_weight="balanced",
            random_state=42,
        )
        self.model.fit(X_scaled, y)
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        joblib.dump(self.model,  MODEL_PATH)
        joblib.dump(self.scaler, SCALER_PATH)

    def is_trained(self) -> bool:
        return self.model is not None


def _dict_to_array(d: dict[str, float]) -> np.ndarray:
    vals = list(d.values())
    return np.array(vals, dtype=np.float32).reshape(1, -1)
