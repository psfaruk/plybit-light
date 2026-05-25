"""
Brain Evolution Engine — Self-Diagnosis & Auto-Adjustment.

Every DIAGNOSIS_INTERVAL seconds it:
  1. Scans SignalHistory for problem patterns
  2. Proposes confidence-threshold adjustments
  3. Identifies bad trading hours (UTC)
  4. Persists an evolution log to SQLite

The main.py signal pipeline reads `get_confidence_multiplier(pair, utc_hour)`
to apply the learned penalty/boost before grading the final signal.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from signal_history import SignalHistory

import candle_db   # for DB_PATH

log = logging.getLogger(__name__)

DIAGNOSIS_INTERVAL = 6 * 3600   # run every 6 hours
MIN_SETTLED        = 8           # need at least N settled signals before judging
BAD_WIN_RATE       = 0.42        # hours below this are penalised
CONF_STEP          = 0.04        # confidence multiplier step per bad finding


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(candle_db.DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_tables() -> None:
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS brain_evolution (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                ts               REAL    NOT NULL,
                problem_type     TEXT    NOT NULL,
                detail           TEXT    NOT NULL,
                action           TEXT    NOT NULL,
                before_winrate   REAL,
                after_winrate    REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS brain_adjustments (
                pair             TEXT    NOT NULL,
                utc_hour         INTEGER NOT NULL,
                multiplier       REAL    NOT NULL DEFAULT 1.0,
                updated_at       REAL    NOT NULL,
                PRIMARY KEY (pair, utc_hour)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_outcomes (
                signal_id  TEXT    PRIMARY KEY,
                pair       TEXT    NOT NULL,
                direction  TEXT    NOT NULL,
                confidence REAL    NOT NULL,
                grade      TEXT    NOT NULL,
                epoch      INTEGER NOT NULL,
                utc_hour   INTEGER NOT NULL,
                outcome    TEXT,
                settled_at REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_so_pair ON signal_outcomes(pair, epoch DESC)")


# ── Module-level init ─────────────────────────────────────────
try:
    _init_tables()
except Exception as e:
    log.warning("brain_evolution: DB init failed: %s", e)


class BrainEvolutionEngine:
    """
    Attach one instance per process; call `record_signal`, `settle_signal`,
    and schedule `run_diagnosis` (or call it manually from a background task).
    """

    def __init__(self) -> None:
        self._last_diagnosis: float = 0.0
        # in-memory adjustment cache: (pair, utc_hour) → multiplier
        self._cache: dict[tuple[str, int], float] = {}
        self._load_cache()

    # ── Public API ───────────────────────────────────────────

    def record_signal(
        self,
        signal_id: str,
        pair: str,
        direction: str,
        confidence: float,
        grade: str,
        epoch: int,
        utc_hour: int,
    ) -> None:
        try:
            with _get_conn() as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO signal_outcomes
                       (signal_id, pair, direction, confidence, grade,
                        epoch, utc_hour)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (signal_id, pair, direction, round(confidence, 4),
                     grade, epoch, utc_hour),
                )
        except Exception as e:
            log.debug("brain_evolution record_signal: %s", e)

    def settle_signal(self, signal_id: str, outcome: str) -> None:
        """Call this when a signal result is known (WIN/LOSS/EXPIRED)."""
        try:
            with _get_conn() as conn:
                conn.execute(
                    """UPDATE signal_outcomes
                       SET outcome = ?, settled_at = ?
                       WHERE signal_id = ?""",
                    (outcome, time.time(), signal_id),
                )
        except Exception as e:
            log.debug("brain_evolution settle: %s", e)

    def get_confidence_multiplier(self, pair: str, utc_hour: int) -> float:
        """
        Returns a multiplier (0.70 – 1.10) to apply to raw confidence.
        Values < 1.0 indicate a historically bad time window for this pair.
        """
        return self._cache.get((pair, utc_hour), 1.0)

    def maybe_run_diagnosis(self) -> None:
        """Call from any background coroutine; self-throttles to DIAGNOSIS_INTERVAL."""
        now = time.time()
        if now - self._last_diagnosis < DIAGNOSIS_INTERVAL:
            return
        self._last_diagnosis = now
        try:
            self._run_diagnosis()
        except Exception as e:
            log.error("brain_evolution diagnosis failed: %s", e)

    # ── Diagnosis ────────────────────────────────────────────

    def _run_diagnosis(self) -> None:
        log.info("Brain Evolution: running self-diagnosis…")
        problems = self._find_problems()

        if not problems:
            log.info("Brain Evolution: no problems found ✓")
            return

        for p in problems:
            log.info("Brain Evolution: %s — %s → %s", p["type"], p["detail"], p["action"])
            self._apply_fix(p)
            self._log_problem(p)

        self._load_cache()
        log.info("Brain Evolution: diagnosis complete, %d adjustments applied", len(problems))

    def _find_problems(self) -> list[dict]:
        problems: list[dict] = []

        try:
            with _get_conn() as conn:
                # ── Bad trading hours ────────────────────────
                rows = conn.execute("""
                    SELECT pair, utc_hour,
                           COUNT(*) as total,
                           SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
                           CAST(SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END)
                                AS REAL) / COUNT(*) as win_rate
                    FROM signal_outcomes
                    WHERE outcome IN ('WIN','LOSS')
                    AND   settled_at >= ?
                    GROUP BY pair, utc_hour
                    HAVING total >= ?
                    AND win_rate < ?
                    ORDER BY win_rate ASC
                """, (time.time() - 7 * 86400, MIN_SETTLED, BAD_WIN_RATE)).fetchall()

                for pair, hour, total, wins, wr in rows:
                    current_mult = self._cache.get((pair, hour), 1.0)
                    new_mult = max(0.70, current_mult - CONF_STEP)
                    problems.append({
                        "type":   "BAD_HOUR",
                        "pair":   pair,
                        "hour":   hour,
                        "detail": f"{pair} UTC-{hour:02d}h: {total} signals, {wr*100:.1f}% WR",
                        "action": f"multiplier {current_mult:.2f} → {new_mult:.2f}",
                        "mult":   new_mult,
                        "wr":     wr,
                    })

                # ── Over-confidence detection ─────────────────
                rows2 = conn.execute("""
                    SELECT pair,
                           CASE
                             WHEN confidence >= 0.90 THEN '90-99'
                             WHEN confidence >= 0.80 THEN '80-89'
                             WHEN confidence >= 0.70 THEN '70-79'
                             ELSE 'below70'
                           END as band,
                           COUNT(*) as total,
                           CAST(SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END)
                                AS REAL) / COUNT(*) as win_rate
                    FROM signal_outcomes
                    WHERE outcome IN ('WIN','LOSS')
                    AND settled_at >= ?
                    GROUP BY pair, band
                    HAVING total >= ? AND win_rate < 0.50
                """, (time.time() - 14 * 86400, MIN_SETTLED)).fetchall()

                for pair, band, total, wr in rows2:
                    problems.append({
                        "type":   "OVERCONFIDENCE",
                        "pair":   pair,
                        "hour":   -1,
                        "detail": f"{pair} confidence {band}%: {wr*100:.1f}% WR on {total} signals",
                        "action": "global confidence threshold raised by 3%",
                        "mult":   0.97,
                        "wr":     wr,
                    })

        except Exception as e:
            log.warning("brain_evolution find_problems: %s", e)

        return problems

    def _apply_fix(self, problem: dict) -> None:
        try:
            if problem["type"] == "BAD_HOUR":
                pair, hour, mult = problem["pair"], problem["hour"], problem["mult"]
                with _get_conn() as conn:
                    conn.execute("""
                        INSERT INTO brain_adjustments (pair, utc_hour, multiplier, updated_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(pair, utc_hour) DO UPDATE SET
                            multiplier=excluded.multiplier,
                            updated_at=excluded.updated_at
                    """, (pair, hour, mult, time.time()))
            elif problem["type"] == "OVERCONFIDENCE":
                # Apply a soft multiplier to ALL hours for this pair
                pair = problem["pair"]
                with _get_conn() as conn:
                    conn.execute("""
                        UPDATE brain_adjustments
                        SET multiplier = MAX(0.70, multiplier * 0.97),
                            updated_at = ?
                        WHERE pair = ?
                    """, (time.time(), pair))
        except Exception as e:
            log.debug("brain_evolution apply_fix: %s", e)

    def _log_problem(self, p: dict) -> None:
        try:
            with _get_conn() as conn:
                conn.execute("""
                    INSERT INTO brain_evolution
                    (ts, problem_type, detail, action, before_winrate)
                    VALUES (?, ?, ?, ?, ?)
                """, (time.time(), p["type"], p["detail"], p["action"], p.get("wr")))
        except Exception as e:
            log.debug("brain_evolution log: %s", e)

    def _load_cache(self) -> None:
        try:
            with _get_conn() as conn:
                rows = conn.execute(
                    "SELECT pair, utc_hour, multiplier FROM brain_adjustments"
                ).fetchall()
            self._cache = {(pair, hour): mult for pair, hour, mult in rows}
        except Exception:
            self._cache = {}

    def get_evolution_log(self, limit: int = 20) -> list[dict]:
        try:
            with _get_conn() as conn:
                rows = conn.execute("""
                    SELECT ts, problem_type, detail, action, before_winrate
                    FROM brain_evolution
                    ORDER BY ts DESC LIMIT ?
                """, (limit,)).fetchall()
            return [
                {"ts": r[0], "type": r[1], "detail": r[2],
                 "action": r[3], "wr": r[4]}
                for r in rows
            ]
        except Exception:
            return []


# ── Singleton ─────────────────────────────────────────────────
brain_evolution = BrainEvolutionEngine()
