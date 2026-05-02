"""5M Window Selector — picks best 3 signals per 5-minute window."""

import time
from config import MAX_SIGNALS_PER_WINDOW, MIN_WINDOW_POSITION, GRADE_MODERATE


POSITION_SCORES: dict[int, float] = {0: 70, 1: 90, 2: 85, 3: 65, 4: 40}


def window_position(epoch: int) -> int:
    """Returns minute index (0-4) within the current 5M window."""
    minute = (epoch // 60) % 5
    return int(minute)


def compute_candle_score(
    mtf_agreement: float,
    position_idx: int,
    reaction_net: float,
    ai_confidence: float,
) -> float:
    pos_score = POSITION_SCORES.get(position_idx, 0.0)
    score = (
        mtf_agreement    * 0.40 * 100 +
        pos_score        * 0.20 +
        reaction_net     * 0.25 +
        ai_confidence    * 0.15 * 100
    )
    return score


class WindowSelector:
    def __init__(self) -> None:
        self._window_signals: list[dict[str, object]] = []
        self._current_window: int = -1

    def try_add(
        self,
        epoch: int,
        confidence: float,
        direction: str,
        grade_str: str,
        mtf_agreement: float,
        reaction_net: float,
    ) -> bool:
        """
        Attempt to register a signal for the 5M window.
        Returns True if accepted (within limit), False if rejected.
        """
        window_id = epoch // 300  # 5-minute window ID
        pos_idx   = window_position(epoch)

        if window_id != self._current_window:
            self._current_window   = window_id
            self._window_signals   = []

        if pos_idx < MIN_WINDOW_POSITION:
            return False

        if confidence < GRADE_MODERATE:
            return False

        score = compute_candle_score(mtf_agreement, pos_idx, reaction_net, confidence)

        if len(self._window_signals) >= MAX_SIGNALS_PER_WINDOW:
            # Replace lowest-score signal if new one is better
            worst_idx = min(range(len(self._window_signals)), key=lambda i: float(self._window_signals[i]["score"]))
            if score <= float(self._window_signals[worst_idx]["score"]):
                return False
            self._window_signals[worst_idx] = {
                "epoch":      epoch,
                "score":      score,
                "confidence": confidence,
                "direction":  direction,
                "grade":      grade_str,
                "pos_idx":    pos_idx,
            }
            return True

        self._window_signals.append({
            "epoch":      epoch,
            "score":      score,
            "confidence": confidence,
            "direction":  direction,
            "grade":      grade_str,
            "pos_idx":    pos_idx,
        })
        return True

    def get_window_plan(self) -> list[dict[str, object]]:
        """Returns accepted signals for the current window, sorted by time."""
        return sorted(self._window_signals, key=lambda x: int(x["epoch"]))

    def window_count(self) -> int:
        return len(self._window_signals)

    def reset(self) -> None:
        self._window_signals = []
        self._current_window = -1
