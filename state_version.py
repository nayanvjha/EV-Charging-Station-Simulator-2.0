from __future__ import annotations

from threading import Lock

_STATE_VERSION = 0
_LOCK = Lock()


def increment_state_version() -> int:
    global _STATE_VERSION
    with _LOCK:
        _STATE_VERSION += 1
        return _STATE_VERSION


def get_state_version() -> int:
    with _LOCK:
        return _STATE_VERSION
