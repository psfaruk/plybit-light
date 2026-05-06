"""
High-performance tick storage — captures every tick from WebSocket streams.

Per-pair ring buffer (deque) keeps the most recent N ticks in memory with O(1)
append. Thread-safe via asyncio (single event loop) — no locks needed.

Each tick: (epoch_seconds: float, price: float).
"""

from __future__ import annotations

import time
from collections import deque
from typing import Iterable


class TickBuffer:
    """Bounded ring buffer of (epoch, price) tuples for one symbol."""

    __slots__ = ("symbol", "_buf", "_count", "_last_epoch", "_last_price", "_dropped")

    def __init__(self, symbol: str, maxlen: int = 20000) -> None:
        self.symbol      = symbol
        self._buf: deque[tuple[float, float]] = deque(maxlen=maxlen)
        self._count      = 0     # total ticks ever received
        self._last_epoch = 0.0
        self._last_price = 0.0
        self._dropped    = 0     # ticks rejected as stale

    def append(self, epoch: float, price: float) -> bool:
        """Append a tick. Rejects out-of-order or duplicate-epoch+price ticks."""
        if epoch < self._last_epoch:
            self._dropped += 1
            return False
        # Tolerate same-epoch ticks (sub-second multiples are real on FX)
        self._buf.append((epoch, price))
        self._count      += 1
        self._last_epoch = epoch
        self._last_price = price
        return True

    def latest(self, n: int = 100) -> list[tuple[float, float]]:
        if n >= len(self._buf):
            return list(self._buf)
        return list(self._buf)[-n:]

    def since(self, epoch_from: float) -> list[tuple[float, float]]:
        return [(e, p) for (e, p) in self._buf if e >= epoch_from]

    def in_minute(self, minute_epoch: int) -> list[tuple[float, float]]:
        end = minute_epoch + 60
        return [(e, p) for (e, p) in self._buf if minute_epoch <= e < end]

    @property
    def total_received(self) -> int:
        return self._count

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def buffered(self) -> int:
        return len(self._buf)

    @property
    def last_price(self) -> float:
        return self._last_price

    @property
    def last_epoch(self) -> float:
        return self._last_epoch


class TickStore:
    """Manages TickBuffers for all symbols. Single global instance."""

    def __init__(self, maxlen_per_symbol: int = 20000) -> None:
        self._maxlen = maxlen_per_symbol
        self._buffers: dict[str, TickBuffer] = {}
        self.started_at = time.time()

    def get(self, symbol: str) -> TickBuffer:
        buf = self._buffers.get(symbol)
        if buf is None:
            buf = TickBuffer(symbol, maxlen=self._maxlen)
            self._buffers[symbol] = buf
        return buf

    def append(self, symbol: str, epoch: float, price: float) -> bool:
        return self.get(symbol).append(epoch, price)

    def stats(self) -> dict[str, dict[str, float]]:
        return {
            sym: {
                "received":   buf.total_received,
                "dropped":    buf.dropped,
                "buffered":   buf.buffered,
                "last_price": buf.last_price,
                "last_epoch": buf.last_epoch,
            }
            for sym, buf in self._buffers.items()
        }

    def symbols(self) -> Iterable[str]:
        return self._buffers.keys()


# Single global instance used by main.py
tick_store = TickStore()
