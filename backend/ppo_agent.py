"""
Model 15: PPO-lite Decision Agent.
Actions: SKIP(0) / GREEN(1) / RED(2)
Only used as a safety gate — if it says SKIP, force skip regardless of AI confidence.
"""

import os
import logging
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import joblib

log = logging.getLogger(__name__)

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
PPO_PATH   = os.path.join(MODELS_DIR, "ppo_agent.pt")
DEVICE     = "cuda" if torch.cuda.is_available() else "cpu"

N_ACTIONS = 3   # SKIP, GREEN, RED
N_FEATURES = 80


class PolicyNetwork(nn.Module):
    def __init__(self, n_feat: int, n_actions: int) -> None:
        super().__init__()
        self.shared = nn.Sequential(
            nn.Linear(n_feat, 128), nn.ReLU(),
            nn.Linear(128, 64),    nn.ReLU(),
        )
        self.actor  = nn.Linear(64, n_actions)
        self.critic = nn.Linear(64, 1)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        h = self.shared(x)
        return self.actor(h), self.critic(h)


class PPOAgent:
    ACTION_MAP = {0: "SKIP", 1: "GREEN", 2: "RED"}

    def __init__(self) -> None:
        self.policy: PolicyNetwork | None = None
        self.trained = False
        self._load()

    def decide(self, features: dict[str, float], confidence: float, direction: str) -> str:
        """
        Returns 'SKIP', 'GREEN', or 'RED'.
        When not trained, defers to the ML ensemble direction.
        """
        if self.policy is None:
            return direction  # not trained yet → pass through

        feat_vec = self._to_tensor(features)
        with torch.no_grad():
            logits, _ = self.policy(feat_vec)
            probs      = torch.softmax(logits, dim=-1)
            action     = int(probs.argmax().item())

        # Override to SKIP if confidence is too low
        if action != 0 and confidence < 0.60:
            return "SKIP"

        return self.ACTION_MAP[action]

    def train_on_history(
        self,
        states: np.ndarray,
        actions: np.ndarray,
        rewards: np.ndarray,
    ) -> float:
        """Simple supervised pre-training on historical trade outcomes."""
        if len(states) < 50:
            return 0.0

        n_feat = states.shape[1]
        self.policy = PolicyNetwork(n_feat, N_ACTIONS).to(DEVICE)
        optimizer   = optim.Adam(self.policy.parameters(), lr=1e-3)
        criterion   = nn.CrossEntropyLoss()

        Xt = torch.tensor(states,  dtype=torch.float32).to(DEVICE)
        at = torch.tensor(actions, dtype=torch.long).to(DEVICE)

        last_acc = 0.0
        for _ in range(30):
            logits, _ = self.policy(Xt)
            loss = criterion(logits, at)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            preds    = logits.argmax(dim=1)
            last_acc = float((preds == at).float().mean())

        self.trained = True
        self._save()
        return last_acc

    def _to_tensor(self, features: dict[str, float]) -> torch.Tensor:
        vals = list(features.values())[:N_FEATURES]
        # Pad or truncate to N_FEATURES
        vals += [0.0] * (N_FEATURES - len(vals))
        return torch.tensor(vals, dtype=torch.float32).unsqueeze(0).to(DEVICE)

    def _save(self) -> None:
        if self.policy is None:
            return
        os.makedirs(MODELS_DIR, exist_ok=True)
        torch.save(self.policy.state_dict(), PPO_PATH)

    def _load(self) -> None:
        if not os.path.exists(PPO_PATH):
            return
        try:
            self.policy = PolicyNetwork(N_FEATURES, N_ACTIONS)
            self.policy.load_state_dict(torch.load(PPO_PATH, map_location="cpu"))
            self.policy.eval()
            self.trained = True
        except Exception as e:
            log.warning("PPO load error: %s", e)
