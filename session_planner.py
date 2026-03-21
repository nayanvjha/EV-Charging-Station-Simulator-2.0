from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, List, Optional

from replay_mode import assert_real_csv_entry_active, is_real_csv_mode

logger = logging.getLogger("session_planner")

DEFAULT_SESSION_POWER_KW = Decimal("11")


@dataclass(frozen=True)
class SessionPlan:
    station_id: str
    start_time: datetime
    end_time: datetime
    duration_sec: int
    total_energy_kWh: Decimal
    avg_power_kW: Decimal


def _parse_timestamp(value: str) -> Optional[datetime]:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _parse_energy(value: str) -> Optional[Decimal]:
    text = value.strip()
    if not text:
        return None
    try:
        energy = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if energy <= 0:
        return None
    return energy


def _duration_seconds(start_time: datetime, end_time: datetime) -> Optional[int]:
    total_seconds = (end_time - start_time).total_seconds()
    if total_seconds <= 0:
        return None
    if total_seconds != int(total_seconds):
        return None
    return int(total_seconds)


def plan_sessions(rows: Iterable[dict]) -> List[SessionPlan]:
    if not is_real_csv_mode():
        raise RuntimeError("SessionPlanner is restricted to REAL_CSV mode")
    assert_real_csv_entry_active()
    plans: List[SessionPlan] = []
    for row_index, row in enumerate(rows, start=2):
        station_id_raw = str(row.get("station_id", "")).strip()
        start_raw = str(row.get("start_time", "")).strip()
        end_raw = str(row.get("end_time", "")).strip()
        energy_raw = str(row.get("total_energy_kWh", "")).strip()

        if not station_id_raw or not start_raw or not end_raw or not energy_raw:
            raise RuntimeError(
                f"Missing required field in CSV row {row_index}"
            )

        station_id = station_id_raw
        start_time = _parse_timestamp(start_raw)
        end_time = _parse_timestamp(end_raw)
        if start_time is None or end_time is None:
            raise RuntimeError(
                f"Invalid timestamp in CSV row {row_index}"
            )

        if end_time <= start_time:
            logger.warning(
                "Dropping CSV row %s: end_time <= start_time",
                row_index,
            )
            continue

        total_energy = _parse_energy(energy_raw)
        if total_energy is None:
            logger.warning(
                "Dropping CSV row %s: total_energy_kWh <= 0",
                row_index,
            )
            continue

        duration_sec = _duration_seconds(start_time, end_time)
        if duration_sec is None:
            logger.warning(
                "Dropping CSV row %s: duration not whole seconds",
                row_index,
            )
            continue

        avg_power = DEFAULT_SESSION_POWER_KW

        plans.append(
            SessionPlan(
                station_id=station_id,
                start_time=start_time,
                end_time=end_time,
                duration_sec=duration_sec,
                total_energy_kWh=total_energy,
                avg_power_kW=avg_power,
            )
        )
    return plans


def plan_sessions_from_csv(path: str | Path) -> List[SessionPlan]:
    if not is_real_csv_mode():
        raise RuntimeError("SessionPlanner is restricted to REAL_CSV mode")
    assert_real_csv_entry_active()
    csv_path = Path(path)
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return []
        return plan_sessions(reader)


__all__ = ["SessionPlan", "plan_sessions", "plan_sessions_from_csv"]
