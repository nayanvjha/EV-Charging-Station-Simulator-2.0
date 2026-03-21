from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Tuple

from csv_loader import ChargingSession, assert_no_load_order_metadata, session_uid
from determinism_guards import assert_no_randomness
from replay_mode import (
    assert_replay_mode_explicit,
    get_real_csv_min_duration_sec,
    get_real_csv_swap_tolerance_sec,
    is_real_csv_mode,
    is_strict_mode,
)
_VALIDATION_INVOCATIONS = 0

logger = logging.getLogger("session_validation")


@dataclass(frozen=True)
class ValidationFailure:
    """Validation failure with context for offline inspection."""
    session_id: str
    station_id: Optional[str]
    reasons: List[str]
    snapshot: Mapping[str, object]


def _get_session_id(session: ChargingSession) -> str:
    return session_uid(session)


def _get_station_id(session: ChargingSession) -> Optional[str]:
    value = session.data.get("station_id")
    if isinstance(value, str) and value:
        return value
    return None


def _is_datetime(value: object) -> bool:
    return isinstance(value, datetime)


def _parse_numeric(value: object) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return None
    return None


def validate_sessions(
    sessions: Iterable[ChargingSession],
    report_path: Optional[str] = None,
) -> Tuple[List[ChargingSession], List[ValidationFailure]]:
    """
    Validate charging sessions and return only SAFE sessions.

    Drops sessions that are unsafe for deterministic replay and records failures.
    """
    assert_replay_mode_explicit()
    assert_no_randomness()

    global _VALIDATION_INVOCATIONS
    if is_strict_mode():
        if _VALIDATION_INVOCATIONS >= 1:
            raise RuntimeError("validate_sessions must be called exactly once per run")
        _VALIDATION_INVOCATIONS += 1

    safe_sessions: List[ChargingSession] = []
    failures: List[ValidationFailure] = []
    total_sessions = 0

    for session in sessions:
        total_sessions += 1
        corrected_data: Optional[dict] = None
        assert_no_load_order_metadata(session)
        reasons: List[str] = []
        start_time = session.data.get("start_time")
        end_time = session.data.get("end_time")

        if not _is_datetime(start_time):
            reasons.append("start_time missing or invalid")
        if not _is_datetime(end_time):
            reasons.append("end_time missing or invalid")

        start_dt: Optional[datetime] = start_time if isinstance(start_time, datetime) else None
        end_dt: Optional[datetime] = end_time if isinstance(end_time, datetime) else None

        if start_dt and end_dt:
            if end_dt < start_dt:
                if is_real_csv_mode():
                    # REAL_CSV TOLERANCE — DO NOT COPY INTO STRICT MODE
                    delta = (start_dt - end_dt).total_seconds()
                    tolerance = get_real_csv_swap_tolerance_sec()
                    if delta <= tolerance:
                        start_dt, end_dt = end_dt, start_dt
                        logger.warning(
                            "Swapped start/end for session %s (delta=%.0fs <= %ss)",
                            _get_session_id(session),
                            delta,
                            tolerance,
                        )
                    else:
                        reasons.append("end_time before start_time beyond tolerance")
                else:
                    reasons.append("end_time is not after start_time")

            if start_dt and end_dt:
                duration = (end_dt - start_dt).total_seconds()
                if duration <= 0:
                    if is_real_csv_mode():
                        # REAL_CSV TOLERANCE — DO NOT COPY INTO STRICT MODE
                        min_duration = get_real_csv_min_duration_sec()
                        end_dt = start_dt + timedelta(seconds=min_duration)
                        logger.warning(
                            "Clamped duration for session %s to %ss",
                            _get_session_id(session),
                            min_duration,
                        )
                    else:
                        reasons.append("end_time is not after start_time")

            if is_real_csv_mode() and isinstance(start_dt, datetime) and isinstance(end_dt, datetime):
                corrected_data = dict(session.data)
                corrected_data["start_time"] = start_dt
                corrected_data["end_time"] = end_dt

        total_energy_kwh = _parse_numeric(session.data.get("total_energy_kwh"))

        if total_energy_kwh is None:
            reasons.append("total_energy_kwh missing")
        elif total_energy_kwh < 0:
            reasons.append("total_energy_kwh is negative")

        if reasons:
            failure = ValidationFailure(
                session_id=_get_session_id(session),
                station_id=_get_station_id(session),
                reasons=reasons,
                snapshot=corrected_data or session.data,
            )
            failures.append(failure)
            logger.warning(
                "Dropped session %s (station=%s): %s",
                failure.session_id,
                failure.station_id or "unknown",
                "; ".join(failure.reasons),
            )
        else:
            safe_sessions.append(ChargingSession(data=corrected_data) if corrected_data else session)

    if total_sessions != len(safe_sessions) + len(failures):
        raise RuntimeError("Validation must be the only place sessions are dropped")

    if report_path:
        _write_report(report_path, failures)

    return safe_sessions, failures


def _write_report(report_path: str, failures: List[ValidationFailure]) -> None:
    path = Path(report_path)
    payload = [
        {
            "session_id": failure.session_id,
            "station_id": failure.station_id,
            "reasons": failure.reasons,
            "snapshot": dict(failure.snapshot),
        }
        for failure in failures
    ]
    path.write_text(json.dumps(payload, default=str, indent=2), encoding="utf-8")
