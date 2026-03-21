from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Iterable

from csv_loader import ChargingSession
from replay_mode import assert_replay_mode_explicit, is_real_csv_mode, is_strict_mode

logger = logging.getLogger("determinism_guards")


def assert_no_randomness() -> None:
    assert_replay_mode_explicit()
    root = Path(__file__).resolve().parent
    excluded_dirs = {
        ".venv",
        "venv",
        "site-packages",
        "__pycache__",
    }
    pattern = re.compile(r"\bimport\s+random\b|\brandom\.")
    for path in root.glob("**/*.py"):
        if any(part in excluded_dirs for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            raise RuntimeError(
                f"Randomness is forbidden in deterministic replay (found in {path.name})"
            )


def assert_no_fallback_defaults(sessions: Iterable[ChargingSession]) -> None:
    assert_replay_mode_explicit()
    if is_real_csv_mode():
        # REAL_CSV TOLERANCE — DO NOT COPY INTO STRICT MODE
        logger.warning(
            "Relaxed guard: fallback defaults allowed for REAL_CSV; fields=%s",
            ["connector_id", "id_tag", "meter_start_wh", "meter_intervals_sec"],
        )
        return
    if not is_strict_mode():
        return
    for session in sessions:
        data = session.data
        _require_field(data, "connector_id")
        _require_field(data, "id_tag")
        _require_field(data, "meter_start_wh")
        _require_field(data, "meter_intervals_sec")


def assert_no_defaults_or_fallbacks() -> None:
    if not is_strict_mode():
        return
    assert_replay_mode_explicit()
    if os.getenv("SIM_REQUIRE_NO_DEFAULT_PROFILES") == "1":
        from profiles import DEFAULT_PROFILES

        if DEFAULT_PROFILES:
            raise RuntimeError("Default profiles are forbidden; CSV-driven profiles required")
        return


def assert_no_wall_clock_usage() -> None:
    assert_replay_mode_explicit()
    root = Path(__file__).resolve().parent
    allowed = {
        "protocol_station.py",
        "security_detection.py",
        "security_monitor.py",
        "csms_server.py",
        "csv_cleaner.py",
        "user_admin.py",
        "db.py",
        "security_pipeline.py",
    }
    excluded_dirs = {
        ".venv",
        "venv",
        "site-packages",
        "__pycache__",
    }
    pattern = re.compile(r"\b(datetime\.now|datetime\.utcnow|time\.monotonic|time\.time)\b")
    for path in root.glob("**/*.py"):
        if any(part in excluded_dirs for part in path.parts):
            continue
        if path.name in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            raise RuntimeError(f"Wall-clock usage forbidden in {path.name}")


def _require_field(data: dict, field: str) -> None:
    value = data.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise RuntimeError(f"CSV-driven value required: {field}")