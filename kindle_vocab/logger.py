"""
Thread-safe forensic logger for debugging pipeline decisions.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any, List


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clip(s: Any, n: int = 500) -> str:
    s = str(s) if s is not None else ""
    return s if len(s) <= n else s[: n - 1] + "…"


class ForensicLogger:
    """Line-oriented, human-readable, thread-safe logger."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._fh = open(path, "w", encoding="utf-8")
        self._raw(f"Kindle Vocab Builder — Forensic Log")
        self._raw(f"Started: {_now()}\n")

    def close(self) -> None:
        with self._lock:
            try:
                self._fh.write(f"\nFinished: {_now()}\n")
            finally:
                self._fh.close()

    def _raw(self, s: str) -> None:
        self._fh.write(s + "\n")
        self._fh.flush()

    def section(self, title: str) -> None:
        with self._lock:
            self._raw(f"\n{'=' * 8} {title} {'=' * 8}")
            self._raw(f"Time: {_now()}")

    def sub(self, title: str) -> None:
        with self._lock:
            self._raw(f"\n-- {title} --")

    def kv(self, key: str, value: Any) -> None:
        with self._lock:
            self._raw(f"{key}: {value}")

    def bullet(self, msg: str) -> None:
        with self._lock:
            self._raw(f"  • {msg}")

    def bullets(self, msgs: List[str]) -> None:
        with self._lock:
            for m in msgs:
                self._raw(f"  • {m}")

    def blank(self) -> None:
        with self._lock:
            self._raw("")