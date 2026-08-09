"""A tiny thread-safe ring buffer that the log panel renders.

The asyncio thread writes and the render thread reads, so every append goes through
a lock. The buffer is bounded: an unbounded log is a memory leak in a program meant
to sit open for hours.

This is the orchestrator's own diagnostics -- bridge lifecycle, SDK errors, gate
decisions -- not agent output. Agent output goes to that agent's Transcript, which
is append-only and per-node precisely so one chatty agent cannot push another
agent's history out of a shared ring.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import IntEnum


class Level(IntEnum):
    DEBUG = 0
    INFO = 1
    WARN = 2
    ERROR = 3


@dataclass(frozen=True, slots=True)
class Entry:
    t: float
    level: Level
    source: str
    text: str


class Log:
    def __init__(self, capacity: int = 2000) -> None:
        self._lock = threading.Lock()
        self._entries: deque[Entry] = deque(maxlen=capacity)
        self._t0 = time.time()
        self._revision = 0

    def _append(self, level: Level, source: str, text: str) -> None:
        with self._lock:
            self._entries.append(Entry(time.time(), level, source, text))
            self._revision += 1
        print(f"[{level.name[0]}] {source}: {text}", flush=True)

    def debug(self, source: str, text: str) -> None:
        self._append(Level.DEBUG, source, text)

    def info(self, source: str, text: str) -> None:
        self._append(Level.INFO, source, text)

    def warn(self, source: str, text: str) -> None:
        self._append(Level.WARN, source, text)

    def error(self, source: str, text: str) -> None:
        self._append(Level.ERROR, source, text)

    def snapshot(self) -> tuple[list[Entry], int]:
        """Copy out under the lock; the UI must never hold the lock while drawing."""
        with self._lock:
            return list(self._entries), self._revision


LOG = Log()
