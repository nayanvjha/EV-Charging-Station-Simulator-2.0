from __future__ import annotations

import csv
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from types import MappingProxyType
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from replay_mode import assert_replay_mode_explicit, get_default_meter_interval_sec, is_real_csv_mode, is_strict_mode

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - fallback for older Python
    ZoneInfo = None  # type: ignore


TimestampValue = Optional[datetime]

logger = logging.getLogger("csv_loader")


@dataclass(frozen=True)
class ChargingSession:
    """Immutable charging session object produced from a single CSV row."""

    data: Mapping[str, object]


FORBIDDEN_LOAD_ORDER_FIELDS = {
    "order_index",
    "load_index",
    "insertion_index",
}


ID_TAG_MAX_LEN = 20
_ID_TAG_ALLOWED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_:.")


REQUIRED_FIELDS = {
    "start_time",
    "end_time",
    "total_energy_kwh",
    "station_id",
    "connector_id",
    "id_tag",
    "meter_start_wh",
    "meter_intervals_sec",
}

NORMALIZED_HEADER_MAP: Mapping[str, str] = {
    "booking_start_time": "start_time",
    "booking_stop_time": "end_time",
    "energy_consumed": "total_energy_kwh",
    "station_name": "station_id",
    "charger_name": "station_id",
    "connector_id": "connector_id",
    "id_tag": "id_tag",
    "user_name": "user_name",
    "vehicle_number": "vehicle_number",
    "booking_id": "booking_id",
    "start_reading": "meter_start_wh",
    "stop_reading": "meter_stop_wh",
    "meter_intervals_sec": "meter_intervals_sec",
    "meter_interval_seconds": "meter_intervals_sec",
    "meter_interval_sec": "meter_intervals_sec",
    "meter_values_interval": "meter_intervals_sec",
    "transaction_id": "transaction_id",
}


def normalize_column_name(name: str) -> str:
    """Normalize a raw CSV column name to a consistent, lowercase identifier."""
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in name.strip().lower())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned


def assert_no_load_order_metadata(session: ChargingSession) -> None:
    for field in FORBIDDEN_LOAD_ORDER_FIELDS:
        if hasattr(session, field):
            raise ValueError(f"Load-order field '{field}' is not allowed on sessions")
    for field in FORBIDDEN_LOAD_ORDER_FIELDS:
        if field in session.data:
            raise ValueError(f"Load-order field '{field}' is not allowed in session data")


def session_uid(session: ChargingSession) -> str:
    value = session.data.get("session_id")
    if isinstance(value, str) and value:
        return value
    fingerprint = _stable_fingerprint(session.data)
    return f"csv:{fingerprint}"


def _stable_fingerprint(data: Mapping[str, object]) -> str:
    parts: List[str] = []
    for key in sorted(data.keys()):
        value = data.get(key)
        if isinstance(value, datetime):
            serialized = value.isoformat()
        else:
            serialized = "" if value is None else str(value)
        parts.append(f"{key}={serialized}")
    payload = "|".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _resolve_timezone(tz_name: str) -> tzinfo:
    if tz_name.upper() == "UTC":
        return timezone.utc
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return timezone.utc


def parse_timestamp(value: str, tz_name: str) -> datetime:
    """Parse a timestamp string into a timezone-aware datetime."""
    value = value.strip()
    tz = _resolve_timezone(tz_name)

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = _parse_with_formats(value)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _parse_with_formats(value: str) -> datetime:
    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d %I:%M %p",
        "%Y/%m/%d %I:%M %p",
        "%Y-%m-%d %I:%M:%S %p",
        "%Y/%m/%d %I:%M:%S %p",
        "%d-%m-%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y %I:%M %p",
        "%d/%m/%Y %I:%M %p",
        "%d-%m-%Y %I:%M:%S %p",
        "%d/%m/%Y %I:%M:%S %p",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unsupported timestamp format: {value}")


def _append_validation_flag(data: Dict[str, object], flag: str) -> None:
    flags = data.get("validation_flags")
    if isinstance(flags, list):
        if flag not in flags:
            flags.append(flag)
    else:
        data["validation_flags"] = [flag]


def _normalize_row(
    row: Mapping[str, str],
    header_map: Mapping[str, str],
    tz_name: str,
    allow_invalid_timestamps: bool,
) -> Mapping[str, object]:
    normalized: Dict[str, object] = {}

    for raw_key, raw_value in row.items():
        normalized_key = normalize_column_name(raw_key)
        if normalized_key not in header_map:
            continue

        key = header_map[normalized_key]
        value = raw_value.strip() if raw_value is not None else ""
        if key in {"start_time", "end_time"} and value:
            if allow_invalid_timestamps:
                normalized[f"{key}_raw"] = value
                try:
                    value_to_store = parse_timestamp(value, tz_name)
                except ValueError:
                    value_to_store = value
                    _append_validation_flag(normalized, f"{key}_parse_error")
            else:
                value_to_store = parse_timestamp(value, tz_name)
        else:
            value_to_store = value

        normalized[key] = value_to_store

    return MappingProxyType(normalized)


def _duration_seconds(start_time: datetime, end_time: datetime) -> int:
    return int((end_time - start_time).total_seconds())


def _derive_missing_fields(data: Dict[str, object]) -> Dict[str, object]:
    if is_strict_mode():
        return data

    # REAL_CSV TOLERANCE — DO NOT COPY INTO STRICT MODE
    raw_id_tag = str(data.get("id_tag") or "").strip()
    if raw_id_tag:
        data["id_tag_original"] = raw_id_tag

    source_value: Optional[str] = None
    for key in ("vehicle_id", "user_id", "booking_id", "vehicle_number", "user_name"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            source_value = value.strip()
            data["id_tag_source"] = source_value
            data["id_tag_source_field"] = key
            break
    if source_value is None:
        source_value = raw_id_tag or None
        if source_value:
            data["id_tag_source"] = source_value
            data["id_tag_source_field"] = "id_tag"
    if source_value is None:
        raise ValueError("Missing required field: id_tag")

    digest = hashlib.sha256(source_value.encode("utf-8")).hexdigest()
    data["id_tag_hash"] = digest
    data["id_tag"] = digest[:ID_TAG_MAX_LEN]

    if "meter_intervals_sec" not in data or not str(data.get("meter_intervals_sec") or "").strip():
        data["meter_intervals_sec"] = str(get_default_meter_interval_sec())

    return data


def _validate_id_tag_strict(value: str) -> None:
    if not value:
        raise ValueError("Missing required field: id_tag")
    if len(value) > ID_TAG_MAX_LEN:
        raise ValueError("id_tag exceeds protocol length")
    if not value.isascii() or any(ch not in _ID_TAG_ALLOWED for ch in value):
        raise ValueError("id_tag contains non-ASCII or disallowed characters")


def _iter_csv_files(
    directory: Optional[str],
    files: Optional[Sequence[str]],
) -> List[Path]:
    if files:
        return [Path(path) for path in files]
    if directory:
        return sorted(Path(directory).glob("*.csv"))
    raise ValueError("Either 'directory' or 'files' must be provided")


def load_sessions(
    directory: Optional[str] = None,
    files: Optional[Sequence[str]] = None,
    timezone_name: str = "UTC",
) -> List[ChargingSession]:
    """
    Load and normalize multiple CSV files into a unified list of sessions.

    One CSV row produces exactly one immutable ChargingSession.
    """
    assert_replay_mode_explicit()
    sessions: List[ChargingSession] = []
    raw_rows: List[Dict[str, object]] = []
    dropped_in_loader = 0
    for path in _iter_csv_files(directory, files):
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                continue
            normalized_headers = [normalize_column_name(name) for name in reader.fieldnames]
            forbidden_headers = {
                header for header in normalized_headers if header in FORBIDDEN_LOAD_ORDER_FIELDS
            }
            if forbidden_headers:
                raise ValueError(
                    f"Load-order columns not allowed in {path.name}: "
                    f"{', '.join(sorted(forbidden_headers))}"
                )
            header_targets = {
                NORMALIZED_HEADER_MAP.get(header)
                for header in normalized_headers
                if header in NORMALIZED_HEADER_MAP
            }
            missing = REQUIRED_FIELDS - header_targets
            if is_real_csv_mode():
                # REAL_CSV TOLERANCE — DO NOT COPY INTO STRICT MODE
                if "id_tag" in missing:
                    missing.remove("id_tag")
                if "meter_intervals_sec" in missing:
                    missing.remove("meter_intervals_sec")
            if missing:
                raise ValueError(
                    f"Missing required columns in {path.name}: {', '.join(sorted(missing))}"
                )
            for row in reader:
                normalized = _normalize_row(
                    row,
                    NORMALIZED_HEADER_MAP,
                    timezone_name,
                    allow_invalid_timestamps=is_real_csv_mode(),
                )
                mutable = dict(normalized)
                start_time = mutable.get("start_time")
                end_time = mutable.get("end_time")
                if isinstance(start_time, datetime) and isinstance(end_time, datetime):
                    duration_seconds = _duration_seconds(start_time, end_time)
                    if duration_seconds <= 0 and is_real_csv_mode():
                        # REAL_CSV TOLERANCE — DO NOT COPY INTO STRICT MODE
                        _append_validation_flag(mutable, "invalid_duration")
                        mutable["duration_seconds_raw"] = duration_seconds
                elif is_real_csv_mode():
                    _append_validation_flag(mutable, "timestamp_missing_or_invalid")
                if is_real_csv_mode() and (not isinstance(start_time, datetime) or not isinstance(end_time, datetime)):
                    _append_validation_flag(mutable, "duration_unparseable")
                mutable = _derive_missing_fields(mutable)
                if is_strict_mode():
                    id_tag_value = str(mutable.get("id_tag") or "").strip()
                    _validate_id_tag_strict(id_tag_value)
                raw_rows.append(mutable)

    for row in raw_rows:
        session = ChargingSession(data=MappingProxyType(row))
        assert_no_load_order_metadata(session)
        sessions.append(session)

    if is_real_csv_mode() and dropped_in_loader:
        raise RuntimeError("REAL_CSV loader dropped sessions; validation must be the only place sessions are dropped")

    return sessions
