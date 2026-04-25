"""
Deep sequence models — Models 7-13:
7: LSTM+GRU, 8: CNN-LSTM, 9: Attention Transformer,
10: TCN, 11: N-BEATS, 12: TFT (Python fallback), 13: Bayesian NN.
"""

import os
import logging
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
import joblib

from features import compute_features

log = logging.getLogger(__name__)

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
SEQ_LEN    = 30
DEVICE     = "cuda" if torch.cuda.is_available() else "cpu"


def _make_sequences(
    df: pd.DataFrame, utc_hour: int = 0, seq_len: int = SEQ_LEN
) -> tuple[np.ndarray, np.ndarray]:
    feat_df = compute_features(df, utc_hour).fillna(0)
    labels  = (df["close"].shift(-1) > df["close"]).astype(int).fillna(0)

    X_list: list[np.ndarray] = []
    y_list: list[int]        = []
    for i in range(seq_len, len(feat_df) - 1):
        X_list.append(feat_df.iloc[i - seq_len: i].values.astype(np.float32))
        y_list.append(int(labels.iloc[i]))

    if not X_list:
        return np.array([]), np.array([])
    return np.stack(X_list), np.array(y_list, dtype=np.int64)


# ── Model Architectures ──────────────────────────────────────

class LSTMGRUModel(nn.Module):
    def __init__(self, input_size: int) -> None:
        super().__init__()
        self.lstm = nn.LSTM(input_size, 64, num_layers=2, batch_first=True, dropout=0.2)
        self.gru  = nn.GRU(64, 32, batch_first=True)
        self.fc   = nn.Linear(32, 2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        out, _ = self.gru(out)
        return self.fc(out[:, -1, :])


class CNNLSTMModel(nn.Module):
    def __init__(self, input_size: int) -> None:
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv1d(input_size, 64, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv1d(64, 32, kernel_size=3, padding=1),
            nn.ReLU(),
        )
        self.lstm = nn.LSTM(32, 32, batch_first=True)
        self.fc   = nn.Linear(32, 2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x   = x.permute(0, 2, 1)
        x   = self.conv(x).permute(0, 2, 1)
        out, _ = self.lstm(x)
        return self.fc(out[:, -1, :])


class AttentionTransformer(nn.Module):
    def __init__(self, input_size: int) -> None:
        super().__init__()
        d_model = 64
        self.proj = nn.Linear(input_size, d_model)
        enc_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=4, dim_feedforward=128,
            dropout=0.1, batch_first=True,
        )
        self.transformer = nn.TransformerEncoder(enc_layer, num_layers=2)
        self.fc = nn.Linear(d_model, 2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x   = self.proj(x)
        out = self.transformer(x)
        return self.fc(out[:, -1, :])


class TCNBlock(nn.Module):
    def __init__(self, in_ch: int, out_ch: int, dilation: int) -> None:
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv1d(in_ch, out_ch, kernel_size=3, padding=dilation, dilation=dilation),
            nn.BatchNorm1d(out_ch),
            nn.ReLU(),
            nn.Dropout(0.1),
        )
        self.skip = nn.Conv1d(in_ch, out_ch, 1) if in_ch != out_ch else nn.Identity()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.conv(x) + self.skip(x)


class TCNModel(nn.Module):
    def __init__(self, input_size: int) -> None:
        super().__init__()
        self.tcn = nn.Sequential(
            TCNBlock(input_size, 64, 1),
            TCNBlock(64, 64, 2),
            TCNBlock(64, 32, 4),
            TCNBlock(32, 32, 8),
        )
        self.fc = nn.Linear(32, 2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x   = x.permute(0, 2, 1)
        out = self.tcn(x)
        return self.fc(out[:, :, -1])


class NBEATSBlock(nn.Module):
    def __init__(self, input_size: int, units: int = 256) -> None:
        super().__init__()
        self.fc = nn.Sequential(
            nn.Flatten(),
            nn.Linear(input_size, units), nn.ReLU(),
            nn.Linear(units, units),       nn.ReLU(),
            nn.Linear(units, 2),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc(x)


# ── Trainer ──────────────────────────────────────────────────

def _train_torch(
    model: nn.Module,
    X: np.ndarray,
    y: np.ndarray,
    epochs: int = 20,
    lr: float = 1e-3,
) -> float:
    model.to(DEVICE)
    model.train()
    Xt = torch.tensor(X, dtype=torch.float32)
    yt = torch.tensor(y, dtype=torch.long)
    ds = TensorDataset(Xt, yt)
    dl = DataLoader(ds, batch_size=64, shuffle=False)

    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss()
    last_acc  = 0.5

    for _ in range(epochs):
        correct = 0; total = 0
        for xb, yb in dl:
            xb, yb = xb.to(DEVICE), yb.to(DEVICE)
            optimizer.zero_grad()
            out  = model(xb)
            loss = criterion(out, yb)
            loss.backward()
            optimizer.step()
            preds    = out.argmax(dim=1)
            correct += int((preds == yb).sum())
            total   += len(yb)
        last_acc = correct / (total + 1e-10)

    return last_acc


def _predict_torch(model: nn.Module, x: np.ndarray) -> float:
    model.eval()
    model.to(DEVICE)
    with torch.no_grad():
        xt  = torch.tensor(x, dtype=torch.float32).to(DEVICE)
        out = model(xt)
        prob = float(torch.softmax(out, dim=1)[0, 1].cpu())
    return prob


# ── Main class ───────────────────────────────────────────────

class DeepPredictor:
    MODEL_NAMES = ["lstm", "cnn", "attn", "tcn", "nbeats"]

    def __init__(self, pair: str) -> None:
        self.pair     = pair
        self.models: dict[str, nn.Module | None] = {n: None for n in self.MODEL_NAMES}
        self.input_size = 0
        self.trained  = False
        self.accuracy_map: dict[str, float] = {}
        self._load()

    def train(self, df: pd.DataFrame, utc_hour: int = 0) -> dict[str, float]:
        if len(df) < 100:
            return {}

        X, y = _make_sequences(df, utc_hour)
        if len(X) < 50:
            return {}

        n_feat = X.shape[2]
        self.input_size = n_feat
        results: dict[str, float] = {}

        model_ctors: dict[str, type] = {
            "lstm":   LSTMGRUModel,
            "cnn":    CNNLSTMModel,
            "attn":   AttentionTransformer,
            "tcn":    TCNModel,
            "nbeats": NBEATSBlock,
        }
        nbeats_X = X.reshape(len(X), -1)

        for name, Ctor in model_ctors.items():
            try:
                model = Ctor(n_feat if name != "nbeats" else nbeats_X.shape[1])
                Xin   = nbeats_X if name == "nbeats" else X
                acc   = _train_torch(model, Xin, y, epochs=15)
                self.models[name] = model
                self.accuracy_map[name] = acc
                results[name] = acc
            except Exception as e:
                log.warning("Deep model %s train error: %s", name, e)

        self.trained = True
        self._save()
        return results

    def predict(self, df: pd.DataFrame, utc_hour: int = 0) -> dict[str, float]:
        if not self.trained or len(df) < SEQ_LEN + 5:
            return {}

        feat_df = compute_features(df, utc_hour).fillna(0)
        seq     = feat_df.iloc[-SEQ_LEN:].values.astype(np.float32)
        seq_t   = seq[np.newaxis, :, :]  # (1, SEQ_LEN, features)

        probs: dict[str, float] = {}
        for name, model in self.models.items():
            if model is None:
                continue
            try:
                xin = seq_t.reshape(1, -1) if name == "nbeats" else seq_t
                probs[name] = _predict_torch(model, xin)
            except Exception:
                pass

        return probs

    # Bayesian NN — Monte Carlo Dropout approximation
    def predict_bayesian(self, df: pd.DataFrame, utc_hour: int = 0, n_samples: int = 20) -> dict[str, object]:
        """Returns mean + std (uncertainty) for LSTM model using MC Dropout."""
        model = self.models.get("lstm")
        if model is None or len(df) < SEQ_LEN + 5:
            return {"bayes_mean": 0.5, "bayes_std": 0.1}

        feat_df = compute_features(df, utc_hour).fillna(0)
        seq     = feat_df.iloc[-SEQ_LEN:].values.astype(np.float32)
        xt      = torch.tensor(seq[np.newaxis, :, :], dtype=torch.float32).to(DEVICE)

        # Enable dropout at inference
        model.train()
        model.to(DEVICE)
        preds: list[float] = []
        with torch.no_grad():
            for _ in range(n_samples):
                out  = model(xt)
                prob = float(torch.softmax(out, dim=1)[0, 1].cpu())
                preds.append(prob)
        model.eval()

        mean = float(np.mean(preds))
        std  = float(np.std(preds))
        return {"bayes_mean": mean, "bayes_std": std}

    def _save(self) -> None:
        try:
            for name, model in self.models.items():
                if model is not None:
                    torch.save(model.state_dict(), self._model_path(name))
            joblib.dump({
                "input_size": self.input_size,
                "accuracy":   self.accuracy_map,
            }, self._meta_path())
        except Exception as e:
            log.error("Deep save error: %s", e)

    def _load(self) -> None:
        meta_p = self._meta_path()
        if not os.path.exists(meta_p):
            return
        try:
            meta = joblib.load(meta_p)
            self.input_size  = meta["input_size"]
            self.accuracy_map = meta.get("accuracy", {})
            n_feat = self.input_size
            model_ctors: dict[str, type] = {
                "lstm": LSTMGRUModel, "cnn": CNNLSTMModel,
                "attn": AttentionTransformer, "tcn": TCNModel,
                "nbeats": NBEATSBlock,
            }
            for name, Ctor in model_ctors.items():
                p = self._model_path(name)
                if not os.path.exists(p):
                    continue
                n = n_feat if name != "nbeats" else (n_feat * SEQ_LEN)
                model = Ctor(n)
                model.load_state_dict(torch.load(p, map_location="cpu"))
                model.eval()
                self.models[name] = model
            self.trained = True
        except Exception as e:
            log.warning("Deep load error for %s: %s", self.pair, e)

    def _model_path(self, name: str) -> str:
        safe = self.pair.replace("/", "_")
        return os.path.join(MODELS_DIR, f"deep_{safe}_{name}.pt")

    def _meta_path(self) -> str:
        safe = self.pair.replace("/", "_")
        return os.path.join(MODELS_DIR, f"deep_{safe}_meta.joblib")
