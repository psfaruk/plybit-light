"""SQLite candle storage — 5000 candles per pair per timeframe."""

import json
import sqlite3
import os
import logging
import time
from typing import Any

log = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "candles.db")
MAX_CANDLES = 5000
MAX_STACK_ROWS = 2000  # per pair


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS candles (
                pair        TEXT    NOT NULL,
                granularity INTEGER NOT NULL,
                epoch       REAL    NOT NULL,
                open        REAL    NOT NULL,
                high        REAL    NOT NULL,
                low         REAL    NOT NULL,
                close       REAL    NOT NULL,
                PRIMARY KEY (pair, granularity, epoch)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_candles_lookup
            ON candles (pair, granularity, epoch DESC)
        """)
        # Adaptive threshold state per (pair, session, vol_bucket)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS adaptive_threshold (
                pair        TEXT NOT NULL,
                session     TEXT NOT NULL,
                vol_bucket  TEXT NOT NULL,
                threshold   REAL NOT NULL,
                wins        INTEGER NOT NULL DEFAULT 0,
                losses      INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (pair, session, vol_bucket)
            )
        """)
        # Stacking ensemble training buffer (prob vectors + labels)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stacking_buffer (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                pair        TEXT    NOT NULL,
                probs       TEXT    NOT NULL,
                label       INTEGER NOT NULL,
                ts          REAL    NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_stack_pair_ts
            ON stacking_buffer (pair, ts)
        """)
        # Agent performance weights (multi-agent self-learning)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_weights (
                pair        TEXT    NOT NULL,
                agent       TEXT    NOT NULL,
                wins        INTEGER NOT NULL DEFAULT 0,
                losses      INTEGER NOT NULL DEFAULT 0,
                total       INTEGER NOT NULL DEFAULT 0,
                recent_json TEXT    NOT NULL DEFAULT '[]',
                PRIMARY KEY (pair, agent)
            )
        """)
    log.info("SQLite candle DB ready: %s", DB_PATH)


# ── Agent weight persistence ──────────────────────────────────

def load_agent_weights() -> dict[tuple[str, str], dict]:
    """Returns {(pair, agent): {wins, losses, total, recent}} for all stored agents."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT pair, agent, wins, losses, total, recent_json FROM agent_weights"
        ).fetchall()
    result = {}
    for pair, agent, wins, losses, total, recent_json in rows:
        try:
            recent = json.loads(recent_json)
        except Exception:
            recent = []
        result[(pair, agent)] = {
            "wins": wins, "losses": losses,
            "total": total, "recent": recent,
        }
    return result


def save_agent_weight(
    pair: str, agent: str,
    wins: int, losses: int, total: int,
    recent: list,
) -> None:
    with _connect() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO agent_weights
              (pair, agent, wins, losses, total, recent_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (pair, agent, wins, losses, total, json.dumps(recent[-30:])))


# ── Adaptive Threshold persistence ───────────────────────────

def load_adaptive_threshold() -> dict[tuple[str, str, str], dict[str, Any]]:
    """Returns {(pair, session, vol_bucket): {threshold, wins, losses}}."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT pair, session, vol_bucket, threshold, wins, losses FROM adaptive_threshold"
        ).fetchall()
    return {
        (r[0], r[1], r[2]): {"threshold": r[3], "wins": r[4], "losses": r[5]}
        for r in rows
    }


def save_adaptive_threshold_key(
    pair: str, session: str, vol_bucket: str,
    threshold: float, wins: int, losses: int,
) -> None:
    with _connect() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO adaptive_threshold
              (pair, session, vol_bucket, threshold, wins, losses)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (pair, session, vol_bucket, threshold, wins, losses))


# ── Stacking buffer persistence ───────────────────────────────

def append_stacking_row(pair: str, probs: list[float], label: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO stacking_buffer (pair, probs, label, ts) VALUES (?, ?, ?, ?)",
            (pair, json.dumps(probs), label, time.time()),
        )
        # Prune oldest rows beyond MAX_STACK_ROWS per pair
        conn.execute("""
            DELETE FROM stacking_buffer
            WHERE pair = ? AND id NOT IN (
                SELECT id FROM stacking_buffer
                WHERE pair = ?
                ORDER BY ts DESC
                LIMIT ?
            )
        """, (pair, pair, MAX_STACK_ROWS))


def load_stacking_buffer(pair: str) -> list[tuple[list[float], int]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT probs, label FROM stacking_buffer WHERE pair=? ORDER BY ts ASC LIMIT ?",
            (pair, MAX_STACK_ROWS),
        ).fetchall()
    result = []
    for probs_json, label in rows:
        try:
            result.append((json.loads(probs_json), int(label)))
        except Exception:
            pass
    return result


def save_candles(pair: str, granularity: int, candles: list[dict[str, Any]]) -> None:
    if not candles:
        return
    rows = [
        (pair, granularity, float(c["epoch"]),
         float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]))
        for c in candles
    ]
    with _connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?)", rows
        )
        # Keep only the most recent MAX_CANDLES per pair/granularity
        conn.execute("""
            DELETE FROM candles
            WHERE pair=? AND granularity=?
              AND epoch NOT IN (
                  SELECT epoch FROM candles
                  WHERE pair=? AND granularity=?
                  ORDER BY epoch DESC
                  LIMIT ?
              )
        """, (pair, granularity, pair, granularity, MAX_CANDLES))


def save_candle(pair: str, granularity: int, candle: dict[str, Any]) -> None:
    save_candles(pair, granularity, [candle])


def load_candles(pair: str, granularity: int, limit: int = MAX_CANDLES) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("""
            SELECT epoch, open, high, low, close
            FROM candles
            WHERE pair=? AND granularity=?
            ORDER BY epoch ASC
            LIMIT ?
        """, (pair, granularity, limit)).fetchall()
    return [
        {"epoch": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4]}
        for r in rows
    ]


def candle_count(pair: str, granularity: int) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM candles WHERE pair=? AND granularity=?",
            (pair, granularity)
        ).fetchone()
    return int(row[0]) if row else 0
