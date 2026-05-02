"""Signal history tracking and self-learning system."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SignalRecord:
    signal_id: str
    pair: str
    direction: str
    confidence: float
    grade: str
    epoch: int
    utc_hour: int
    outcome: Optional[str] = None      # "WIN" | "LOSS" | "EXPIRED"
    settled_at: Optional[float] = None


class SignalHistory:
    def __init__(self, max_records: int = 500) -> None:
        self._records: dict[str, SignalRecord] = {}
        self._by_pair: dict[str, list[str]] = {}
        self._max = max_records

    def add(
        self,
        signal_id: str,
        pair: str,
        direction: str,
        confidence: float,
        grade: str,
        epoch: int,
        utc_hour: int,
    ) -> None:
        rec = SignalRecord(
            signal_id=signal_id,
            pair=pair,
            direction=direction,
            confidence=confidence,
            grade=grade,
            epoch=epoch,
            utc_hour=utc_hour,
        )
        self._records[signal_id] = rec
        self._by_pair.setdefault(pair, []).append(signal_id)
        self._evict()

    def settle(self, signal_id: str, outcome: str) -> bool:
        """Mark a signal WIN/LOSS/EXPIRED. Returns True if found."""
        rec = self._records.get(signal_id)
        if rec is None:
            return False
        rec.outcome = outcome
        rec.settled_at = time.time()
        return True

    def auto_settle_expired(self, pair: str, current_epoch: int, expiry_bars: int = 1, bar_sec: int = 60) -> int:
        """Auto-expire pending signals older than expiry_bars × bar_sec. Returns count expired."""
        count = 0
        cutoff = current_epoch - (expiry_bars * bar_sec)
        for sid in self._by_pair.get(pair, []):
            rec = self._records.get(sid)
            if rec and rec.outcome is None and rec.epoch < cutoff:
                rec.outcome = "EXPIRED"
                rec.settled_at = time.time()
                count += 1
        return count

    def get_win_rate(self, pair: str, min_settled: int = 5) -> Optional[float]:
        """Returns win rate [0, 1] for settled signals of this pair, or None if too few."""
        settled = [
            self._records[sid]
            for sid in self._by_pair.get(pair, [])
            if self._records.get(sid) and self._records[sid].outcome in ("WIN", "LOSS")
        ]
        if len(settled) < min_settled:
            return None
        wins = sum(1 for r in settled if r.outcome == "WIN")
        return wins / len(settled)

    def get_bad_patterns(self, pair: str, min_signals: int = 3, max_win_rate: float = 0.35) -> set[int]:
        """Returns UTC hours with win_rate ≤ max_win_rate (based on ≥ min_signals settled)."""
        hour_results: dict[int, list[str]] = {}
        for sid in self._by_pair.get(pair, []):
            rec = self._records.get(sid)
            if rec and rec.outcome in ("WIN", "LOSS"):
                hour_results.setdefault(rec.utc_hour, []).append(rec.outcome)

        bad: set[int] = set()
        for h, outcomes in hour_results.items():
            if len(outcomes) >= min_signals:
                wr = outcomes.count("WIN") / len(outcomes)
                if wr <= max_win_rate:
                    bad.add(h)
        return bad

    def get_stats(self, pair: str) -> dict[str, object]:
        ids = self._by_pair.get(pair, [])
        recs = [self._records[s] for s in ids if s in self._records]
        total = len(recs)
        settled = [r for r in recs if r.outcome in ("WIN", "LOSS")]
        wins = sum(1 for r in settled if r.outcome == "WIN")
        return {
            "pair": pair,
            "total": total,
            "settled": len(settled),
            "wins": wins,
            "losses": len(settled) - wins,
            "win_rate": round(wins / len(settled), 3) if settled else None,
            "pending": sum(1 for r in recs if r.outcome is None),
        }

    def get_records(self, pair: str, limit: int = 50) -> list[dict[str, object]]:
        ids = self._by_pair.get(pair, [])[-limit:]
        out = []
        for sid in reversed(ids):
            rec = self._records.get(sid)
            if rec:
                out.append({
                    "signal_id":  rec.signal_id,
                    "direction":  rec.direction,
                    "confidence": round(rec.confidence, 3),
                    "grade":      rec.grade,
                    "epoch":      rec.epoch,
                    "utc_hour":   rec.utc_hour,
                    "outcome":    rec.outcome,
                    "settled_at": rec.settled_at,
                })
        return out

    def _evict(self) -> None:
        if len(self._records) <= self._max:
            return
        oldest = sorted(self._records.values(), key=lambda r: r.epoch)
        for rec in oldest[: len(self._records) - self._max]:
            self._records.pop(rec.signal_id, None)
            for ids in self._by_pair.values():
                if rec.signal_id in ids:
                    ids.remove(rec.signal_id)
