from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from typing import Iterable, List, Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


OUTPUT_FILE = "cleaned_sessions.csv"
META_FILE = "cleaned_sessions.meta"
TARGET_TIMEZONE = "UTC"
DEFAULT_MAX_ENERGY_KWH = 200.0
DEFAULT_MAX_AVG_POWER_KW = 350.0
ENERGY_UNIT_ENV = "CSV_ENERGY_UNIT"
ENERGY_COLUMN_ENV = "CSV_ENERGY_COLUMN"
MAX_ENERGY_ENV = "CSV_MAX_ENERGY_KWH"
MAX_AVG_POWER_ENV = "CSV_MAX_AVG_POWER_KW"


@dataclass(frozen=True)
class CleanedRow:
    station_id: str
    start_time: str
    end_time: str
    total_energy_kWh: str


def normalize_header(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in name.strip().lower())
    return "_".join(part for part in cleaned.split("_") if part)


def resolve_timezone() -> Optional[tzinfo]:
    if TARGET_TIMEZONE.upper() == "UTC":
        return timezone.utc
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(TARGET_TIMEZONE)
    except Exception:
        return None


def parse_timestamp(value: str, target_tz: tzinfo) -> Optional[str]:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed: Optional[datetime] = None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
    if parsed is None:
        for fmt in (
            "%Y-%m-%d %I:%M %p",
            "%Y-%m-%d %I:%M:%S %p",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=target_tz)
    try:
        normalized = parsed.astimezone(target_tz)
    except Exception:
        return None
    return normalized.isoformat()


def normalize_station_id(raw: str) -> Optional[str]:
    value = raw.strip()
    if not value:
        return None
    normalized = value.upper()
    return normalized if normalized else None


def extract_value(row: dict, keys: Iterable[str]) -> Optional[str]:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _parse_energy_unit(value: Optional[str]) -> str:
    if value is None:
        raise ValueError(
            "Unit conversion is forbidden. Unit declaration missing. "
            "Pass --energy-unit kwh and export the CSV with energy already in kWh."
        )
    unit = value.strip().lower()
    if unit != "kwh":
        raise ValueError(
            "Unit conversion is forbidden. Unsupported unit declared. "
            "Pass --energy-unit kwh and export the CSV with energy already in kWh."
        )
    return unit


def _get_env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"Invalid {name} value: {raw}")


def _resolve_energy_unit(default_unit: Optional[str]) -> str:
    return _parse_energy_unit(default_unit)


def _validate_energy(
    value: float,
    unit: str,
    max_energy_kwh: float,
    duration_hours: float,
    max_avg_power_kw: float,
) -> float:
    if value <= 0:
        raise ValueError("Energy must be positive")
    if unit != "kwh":
        raise ValueError(
            "Unit conversion is forbidden. Invalid unit encountered. "
            "Pass --energy-unit kwh and export the CSV with energy already in kWh."
        )
    if value > max_energy_kwh:
        raise ValueError("Energy exceeds max kWh limit")
    energy_kwh = value

    if duration_hours <= 0:
        raise ValueError("Session duration must be positive")
    avg_power_kw = energy_kwh / duration_hours
    if avg_power_kw > max_avg_power_kw:
        raise ValueError("Average power exceeds max limit")
    return energy_kwh


def iter_input_files(args: List[str]) -> List[Path]:
    if args:
        return [Path(path) for path in args]
    return sorted(Path.cwd().glob("Completed_Bookings_*.csv"))


def clean_file(
    path: Path,
    target_tz: tzinfo,
    energy_column: Optional[str] = None,
    energy_unit: Optional[str] = None,
    max_energy_kwh: Optional[float] = None,
    max_avg_power_kw: Optional[float] = None,
) -> List[CleanedRow]:
    if not energy_column:
        raise ValueError("Energy column missing: provide --energy-column or CSV_ENERGY_COLUMN")
    resolved_unit = _resolve_energy_unit(energy_unit)
    max_energy_value = max_energy_kwh if max_energy_kwh is not None else _get_env_float(
        MAX_ENERGY_ENV,
        DEFAULT_MAX_ENERGY_KWH,
    )
    max_avg_power_value = (
        max_avg_power_kw
        if max_avg_power_kw is not None
        else _get_env_float(MAX_AVG_POWER_ENV, DEFAULT_MAX_AVG_POWER_KW)
    )
    rows: List[CleanedRow] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return rows
        energy_header = normalize_header(energy_column)
        header_set = {normalize_header(name) for name in reader.fieldnames}
        if energy_header not in header_set:
            raise ValueError(
                f"Energy column '{energy_column}' not found in CSV header"
            )
        for row_index, raw_row in enumerate(reader, start=2):
            normalized = {normalize_header(k): v for k, v in raw_row.items()}

            station_raw = extract_value(
                normalized,
                ("station_id", "charger_name", "station_name"),
            )
            station_id = normalize_station_id(station_raw or "")
            if not station_id:
                continue

            start_raw = extract_value(
                normalized,
                ("start_time", "booking_start_time"),
            )
            end_raw = extract_value(
                normalized,
                ("end_time", "booking_stop_time"),
            )
            if not start_raw or not end_raw:
                continue

            start_iso = parse_timestamp(start_raw, target_tz)
            end_iso = parse_timestamp(end_raw, target_tz)
            if not start_iso or not end_iso:
                continue

            try:
                start_dt = datetime.fromisoformat(start_iso)
                end_dt = datetime.fromisoformat(end_iso)
            except ValueError:
                continue
            if end_dt < start_dt:
                continue
            duration_hours = (end_dt - start_dt).total_seconds() / 3600.0
            if duration_hours <= 0:
                continue

            energy_raw = extract_value(
                normalized,
                (energy_header,),
            )
            if energy_raw is None:
                raise ValueError("Energy value missing")
            try:
                energy = float(energy_raw)
            except ValueError:
                raise ValueError("Energy value invalid")
            if energy <= 0:
                continue

            energy_kwh = _validate_energy(
                value=energy,
                unit=resolved_unit,
                max_energy_kwh=max_energy_value,
                duration_hours=duration_hours,
                max_avg_power_kw=max_avg_power_value,
            )

            rows.append(
                CleanedRow(
                    station_id=station_id,
                    start_time=start_iso,
                    end_time=end_iso,
                    total_energy_kWh=f"{energy_kwh:.6f}",
                )
            )
    return rows


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean charging sessions CSV")
    parser.add_argument("files", nargs="*", help="Input CSV files")
    parser.add_argument("--energy-column", required=True, help="Energy column name")
    parser.add_argument("--energy-unit", required=True, help="Energy unit (kwh)")
    parser.add_argument("--max-energy-kwh", type=float, help="Max energy per session (kWh)")
    parser.add_argument(
        "--max-avg-power-kw",
        type=float,
        help="Max average power per session (kW)",
    )
    return parser.parse_args(argv)


def main() -> int:
    target_tz = resolve_timezone()
    if target_tz is None:
        raise RuntimeError("Target timezone could not be resolved")

    args = _parse_args(sys.argv[1:])
    energy_unit = args.energy_unit
    energy_column = args.energy_column
    input_files = iter_input_files(args.files)
    cleaned: List[CleanedRow] = []
    for path in input_files:
        cleaned.extend(
            clean_file(
                path,
                target_tz,
                energy_column=energy_column,
                energy_unit=energy_unit,
                max_energy_kwh=args.max_energy_kwh,
                max_avg_power_kw=args.max_avg_power_kw,
            )
        )

    output_path = Path.cwd() / OUTPUT_FILE
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["station_id", "start_time", "end_time", "total_energy_kWh"])
        for row in cleaned:
            writer.writerow([
                row.station_id,
                row.start_time,
                row.end_time,
                row.total_energy_kWh,
            ])
    meta_path = Path.cwd() / META_FILE
    meta_path.write_text("CLEANED_CSV_V1", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
