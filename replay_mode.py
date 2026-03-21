from __future__ import annotations

import os
from enum import Enum
from typing import Optional


class ReplayMode(str, Enum):
    STRICT = "STRICT"
    REAL_CSV = "REAL_CSV"


_MODE: ReplayMode = ReplayMode.STRICT
_EXPLICIT_SET: bool = False
_DEFAULT_METER_INTERVAL_SEC: int = 60
_REAL_CSV_SWAP_TOLERANCE_SEC: int = 300
_REAL_CSV_MIN_DURATION_SEC: int = 60
_REAL_CSV_ENTRY_ACTIVE: bool = False
_BANNER_EMITTED: bool = False


def _init_from_env() -> None:
    global _MODE, _EXPLICIT_SET
    value = os.getenv("REPLAY_MODE")
    if not value:
        return
    normalized = value.strip().upper()
    try:
        _MODE = ReplayMode(normalized)
    except ValueError as exc:
        raise RuntimeError(f"Unknown REPLAY_MODE: {value}") from exc
    _EXPLICIT_SET = True


_init_from_env()


def set_replay_mode(mode: ReplayMode) -> None:
    global _MODE, _EXPLICIT_SET
    env_value = os.getenv("REPLAY_MODE")
    if env_value:
        normalized = env_value.strip().upper()
        try:
            env_mode = ReplayMode(normalized)
        except ValueError as exc:
            raise RuntimeError(f"Unknown REPLAY_MODE: {env_value}") from exc
        if env_mode != mode:
            raise RuntimeError("Replay mode cannot be switched at runtime")
    _MODE = mode
    _EXPLICIT_SET = True


def get_replay_mode() -> ReplayMode:
    return _MODE


def get_default_meter_interval_sec() -> int:
    return _DEFAULT_METER_INTERVAL_SEC


def get_real_csv_swap_tolerance_sec() -> int:
    return _REAL_CSV_SWAP_TOLERANCE_SEC


def get_real_csv_min_duration_sec() -> int:
    return _REAL_CSV_MIN_DURATION_SEC


def is_strict_mode() -> bool:
    return _MODE == ReplayMode.STRICT


def is_real_csv_mode() -> bool:
    return _MODE == ReplayMode.REAL_CSV


def assert_replay_mode_explicit() -> None:
    if not _EXPLICIT_SET:
        raise RuntimeError("Replay mode must be explicitly set before replay starts")


def is_replay_mode_explicit() -> bool:
    return _EXPLICIT_SET


def begin_real_csv_entry() -> None:
    global _REAL_CSV_ENTRY_ACTIVE
    import inspect
    caller = inspect.stack()[1].frame
    module = inspect.getmodule(caller)
    if module is None or module.__name__ != "replay_integration":
        raise RuntimeError("REAL_CSV entry is restricted to run_real_csv_replay")
    if _REAL_CSV_ENTRY_ACTIVE:
        raise RuntimeError("REAL_CSV entry already active")
    _REAL_CSV_ENTRY_ACTIVE = True


def end_real_csv_entry() -> None:
    global _REAL_CSV_ENTRY_ACTIVE
    _REAL_CSV_ENTRY_ACTIVE = False


def assert_real_csv_entry_active() -> None:
    if not _REAL_CSV_ENTRY_ACTIVE:
        raise RuntimeError("REAL_CSV replay must be started via the explicit entry point")


def is_real_csv_entry_active() -> bool:
    return _REAL_CSV_ENTRY_ACTIVE


def log_mode_banner(logger) -> None:
    global _BANNER_EMITTED
    if not _EXPLICIT_SET:
        return
    if _BANNER_EMITTED:
        return
    if is_strict_mode():
        logger.warning("RUNNING IN STRICT MODE — CORRECTNESS ONLY")
    else:
        logger.warning("RUNNING IN REAL_CSV MODE — SIMULATION / REALISM")
        logger.warning("REAL_CSV MODE IS NOT FOR CORRECTNESS VALIDATION")
        logger.warning("RESULTS MAY DIFFER FROM STRICT MODE BY DESIGN")
    _BANNER_EMITTED = True
