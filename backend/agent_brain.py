"""
Shared Brain — central memory for the multi-agent trading system.

Every agent writes its AgentResult here after computing.
Every agent can read what others computed.
CriticAgent reads all and detects conflicts.
MetaController reads all and makes the final weighted decision.
Outcomes (WIN/LOSS) feed back to update each agent's dynamic weight.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Callable

log = logging.getLogger(__name__)

# Injected by main.py after DB is ready — avoids circular import
_save_weight_fn: Optional[Callable] = None

def set_weight_persistence(fn: Callable) -> None:
    """Called once at startup: fn(pair, agent, wins, losses, total, recent)."""
    global _save_weight_fn
    _save_weight_fn = fn


# ── Data classes ──────────────────────────────────────────────────

@dataclass
class AgentResult:
    """Result produced by one agent for one candle."""
    agent:      str        # e.g. "smc", "ict", "ml", "volume"
    pair:       str
    epoch:      int        # candle epoch this analysis is for
    direction:  str        # "GREEN" | "RED" | "NEUTRAL"
    confidence: float      # 0.0–1.0
    score:      float      # -1.0 (strong RED) → +1.0 (strong GREEN)
    reasons:    list[str]  # human-readable reasoning
    data:       dict       # raw indicator values
    ts:         float = field(default_factory=time.time)


@dataclass
class AgentPerf:
    """Rolling performance tracker for one agent on one pair."""
    wins:   int = 0
    losses: int = 0
    total:  int = 0
    _recent: list = field(default_factory=list)  # True=win, last WINDOW trades
    WINDOW: int = field(default=30, init=False, repr=False)

    @property
    def win_rate(self) -> float:
        if not self._recent:
            return 0.50
        return sum(self._recent) / len(self._recent)

    @property
    def weight(self) -> float:
        """
        Dynamic weight based on recent win rate.
          50% WR  →  1.00  (neutral)
          70% WR  →  1.80  (rewarded)
          30% WR  →  0.20  (penalised)
        """
        if len(self._recent) < 5:
            return 1.0  # not enough data — neutral
        wr = self.win_rate
        return max(0.20, min(2.0, 1.0 + (wr - 0.50) * 4.0))

    def record(self, is_win: bool) -> None:
        self.total += 1
        if is_win:
            self.wins += 1
        else:
            self.losses += 1
        self._recent.append(is_win)
        if len(self._recent) > self.WINDOW:
            self._recent.pop(0)


# ── Shared Brain ─────────────────────────────────────────────────

class SharedBrain:
    """
    Central memory store for multi-agent analysis.

    Stores:
      - Latest AgentResult per (pair, agent_name)
      - Performance history per (pair, agent_name) — drives dynamic weights
      - Historical epoch snapshots for outcome recording
    """

    def __init__(self) -> None:
        # pair → agent_name → AgentResult  (latest per agent)
        self._latest: dict[str, dict[str, AgentResult]] = {}
        # pair → agent_name → AgentPerf
        self._perf: dict[str, dict[str, AgentPerf]] = {}
        # pair → epoch → {agent_name: direction}  (for outcome tracking)
        self._history: dict[str, dict[int, dict[str, str]]] = {}

    def load_from_db(self, rows: dict[tuple[str, str], dict]) -> None:
        """Restore persisted agent weights on startup."""
        for (pair, agent), data in rows.items():
            perf = self._perf.setdefault(pair, {}).setdefault(agent, AgentPerf())
            perf.wins   = data.get("wins",   0)
            perf.losses = data.get("losses", 0)
            perf.total  = data.get("total",  0)
            recent = data.get("recent", [])
            perf._recent = [bool(x) for x in recent[-perf.WINDOW:]]
        log.info("Brain: loaded weights for %d agent×pair slots", len(rows))

    # ── Write ──────────────────────────────────────────────────────

    def write(self, result: AgentResult) -> None:
        """Agent publishes its analysis result."""
        p = result.pair
        self._latest.setdefault(p, {})[result.agent] = result
        # Remember direction prediction for this epoch
        self._history.setdefault(p, {}).setdefault(result.epoch, {})[result.agent] = result.direction
        # Prune old epochs (keep last 200)
        epochs = sorted(self._history[p].keys())
        if len(epochs) > 200:
            for old in epochs[:-200]:
                del self._history[p][old]

    # ── Read ───────────────────────────────────────────────────────

    def read_all(self, pair: str) -> dict[str, AgentResult]:
        """Read every agent's latest result for this pair."""
        return dict(self._latest.get(pair, {}))

    def read(self, pair: str, agent: str) -> Optional[AgentResult]:
        """Read a specific agent's latest result."""
        return self._latest.get(pair, {}).get(agent)

    # ── Weights ────────────────────────────────────────────────────

    def get_weight(self, pair: str, agent: str) -> float:
        return self._perf.get(pair, {}).get(agent, AgentPerf()).weight

    def get_all_weights(self, pair: str) -> dict[str, float]:
        return {a: self.get_weight(pair, a) for a in self._latest.get(pair, {})}

    # ── Outcome feedback ───────────────────────────────────────────

    def record_outcome(
        self,
        pair:             str,
        epoch:            int,
        signal_direction: str,
        outcome:          str,
    ) -> None:
        """
        Called after trade settles (WIN/LOSS).
        Credits each agent whose direction matched the winning direction.
        An agent is "correct" if:
          - Signal WON  and agent agreed with signal direction
          - Signal LOST and agent opposed signal direction (it was right to warn)
        """
        agent_dirs = self._history.get(pair, {}).get(epoch, {})
        if not agent_dirs:
            return

        is_win = outcome == "WIN"
        updated: dict[str, str] = {}

        for agent_name, agent_dir in agent_dirs.items():
            if agent_dir == "NEUTRAL":
                continue  # neutral agents don't get scored
            agent_correct = (
                (is_win     and agent_dir == signal_direction) or
                (not is_win and agent_dir != signal_direction)
            )
            perf = self._perf.setdefault(pair, {}).setdefault(agent_name, AgentPerf())
            perf.record(agent_correct)
            updated[agent_name] = f"{'WIN' if agent_correct else 'LOSS'} w={perf.weight:.2f}"
            # Persist weight update
            if _save_weight_fn is not None:
                try:
                    _save_weight_fn(pair, agent_name, perf.wins, perf.losses, perf.total, perf._recent)
                except Exception as _db_err:
                    log.warning("Brain: weight save failed: %s", _db_err)

        log.debug("Brain outcome %s %s@%d: %s", pair, outcome, epoch, updated)

    # ── Analysis helpers ───────────────────────────────────────────

    def conflict_score(self, pair: str, epoch: int) -> float:
        """
        How much do agents disagree?  0.0 = unanimous, 1.0 = maximally split.
        Only counts non-NEUTRAL agents for this specific epoch.
        """
        results = {
            a: r for a, r in self._latest.get(pair, {}).items()
            if r.epoch == epoch and r.direction != "NEUTRAL"
        }
        if len(results) < 2:
            return 0.0
        green = sum(1 for r in results.values() if r.direction == "GREEN")
        red   = sum(1 for r in results.values() if r.direction == "RED")
        total = green + red
        if total == 0:
            return 0.0
        return 1.0 - max(green, red) / total

    def weighted_votes(self, pair: str, epoch: int) -> tuple[float, float]:
        """
        (green_score, red_score) — each agent weighted by performance × confidence.
        """
        green_wt = red_wt = 0.0
        for agent_name, result in self._latest.get(pair, {}).items():
            if result.epoch != epoch:
                continue
            w = self.get_weight(pair, agent_name) * result.confidence
            if result.direction == "GREEN":
                green_wt += w
            elif result.direction == "RED":
                red_wt += w
        return green_wt, red_wt

    def summary_for_pair(self, pair: str) -> dict:
        """Full brain snapshot — sent to frontend."""
        out: dict[str, dict] = {}
        for agent_name, result in self._latest.get(pair, {}).items():
            perf = self._perf.get(pair, {}).get(agent_name, AgentPerf())
            out[agent_name] = {
                "direction":  result.direction,
                "confidence": round(result.confidence, 3),
                "score":      round(result.score, 3),
                "reasons":    result.reasons[:4],
                "weight":     round(self.get_weight(pair, agent_name), 2),
                "win_rate":   round(perf.win_rate, 3),
                "trades":     perf.total,
                "data":       result.data,
            }
        return out


# ── Module-level singleton (imported everywhere) ──────────────────
shared_brain = SharedBrain()
