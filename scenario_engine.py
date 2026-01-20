import argparse
import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

from controller_api import CSMS_URL, StationManager
from profiles import DEFAULT_PROFILES
import controller_api


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scenario_engine")

SUPPORTED_ACTIONS = {
    "start_stations",
    "stop_stations",
    "set_price",
    "apply_profile",
    "clear_profile",
    "scale_stations",
}


class ScenarioRunner:
    def __init__(
        self,
        manager: Optional[StationManager] = None,
        dry_run: bool = False,
    ) -> None:
        self.manager = manager or StationManager(CSMS_URL, DEFAULT_PROFILES)
        self.dry_run = dry_run
        self._scenario_start = None

    def load_scenario(self, file_path: str) -> List[Dict[str, Any]]:
        data = _load_scenario_file(file_path)
        events = _normalize_events(data)
        _validate_events(events)
        return sorted(events, key=lambda ev: ev["time"])

    async def run(self, events: List[Dict[str, Any]], base_dir: Optional[str] = None) -> None:
        self._scenario_start = time.monotonic()
        last_time = 0.0

        for event in events:
            event_time = float(event["time"])
            delay = max(0.0, event_time - last_time)
            if not self.dry_run and delay > 0:
                await asyncio.sleep(delay)
            last_time = event_time

            await self._execute_event(event, base_dir=base_dir)

    async def _execute_event(self, event: Dict[str, Any], base_dir: Optional[str] = None) -> None:
        action = event["action"]
        timestamp = _format_time(event["time"])

        try:
            if action == "start_stations":
                count = int(event["count"])
                profile = event.get("profile", "default")
                self._log(f"[{timestamp}] Start stations count={count} profile={profile}")
                if not self.dry_run:
                    await self.manager.scale_to(count, profile)

            elif action == "scale_stations":
                new_total = int(event["new_total"])
                profile = event.get("profile", "default")
                self._log(f"[{timestamp}] Scale stations total={new_total} profile={profile}")
                if not self.dry_run:
                    await self.manager.scale_to(new_total, profile)

            elif action == "stop_stations":
                ids = event.get("ids")
                count = event.get("count")
                if ids:
                    self._log(f"[{timestamp}] Stop stations ids={ids}")
                    if not self.dry_run:
                        for station_id in ids:
                            await self.manager.stop_station(station_id)
                else:
                    count = int(count)
                    self._log(f"[{timestamp}] Stop stations count={count}")
                    if not self.dry_run:
                        active_ids = list(self.manager.tasks.keys())
                        for station_id in active_ids[:count]:
                            await self.manager.stop_station(station_id)

            elif action == "set_price":
                value = float(event["value"])
                self._log(f"[{timestamp}] Set price = {value}")
                if not self.dry_run:
                    controller_api.CURRENT_PRICE_PER_KWH = value

            elif action == "apply_profile":
                station_id = event["station_id"]
                profile_path = _resolve_path(event["profile_file"], base_dir)
                connector_id = int(event.get("connector_id", 1))
                self._log(
                    f"[{timestamp}] Apply profile station_id={station_id} profile_file={profile_path}"
                )
                if not self.dry_run:
                    profile_data = _load_profile_file(profile_path)
                    chargepoint = self.manager.station_chargepoints.get(station_id)
                    if not chargepoint:
                        raise ValueError(f"Station {station_id} not connected")
                    await chargepoint.send_charging_profile_to_station(
                        connector_id=connector_id,
                        profile_dict=profile_data,
                    )

            elif action == "clear_profile":
                station_id = event["station_id"]
                self._log(f"[{timestamp}] Clear profile station_id={station_id}")
                if not self.dry_run:
                    chargepoint = self.manager.station_chargepoints.get(station_id)
                    if not chargepoint:
                        raise ValueError(f"Station {station_id} not connected")
                    await chargepoint.clear_charging_profile_from_station()

            else:
                raise ValueError(f"Unsupported action: {action}")

        except Exception as exc:
            self._log(f"[{timestamp}] Error executing {action}: {exc}")

    def _log(self, message: str) -> None:
        logger.info(message)


def _load_scenario_file(file_path: str) -> Any:
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Scenario file not found: {file_path}")

    _, ext = os.path.splitext(file_path.lower())
    with open(file_path, "r", encoding="utf-8") as f:
        if ext in {".yaml", ".yml"}:
            if yaml is None:
                raise ValueError("PyYAML is required to load YAML scenarios")
            return yaml.safe_load(f)
        return json.load(f)


def _normalize_events(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict) and "events" in data:
        data = data["events"]
    if not isinstance(data, list):
        raise ValueError("Scenario file must contain a list of events")
    return data


def _validate_events(events: List[Dict[str, Any]]) -> None:
    for idx, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError(f"Event #{idx} must be an object")
        if "time" not in event:
            raise ValueError(f"Event #{idx} is missing 'time'")
        if "action" not in event:
            raise ValueError(f"Event #{idx} is missing 'action'")

        time_val = event["time"]
        if not isinstance(time_val, (int, float)) or time_val < 0:
            raise ValueError(f"Event #{idx} has invalid 'time': {time_val}")

        action = event["action"]
        if action not in SUPPORTED_ACTIONS:
            raise ValueError(f"Event #{idx} has unsupported action: {action}")

        if action in {"start_stations"}:
            if "count" not in event:
                raise ValueError(f"Event #{idx} start_stations requires 'count'")
        if action in {"scale_stations"}:
            if "new_total" not in event:
                raise ValueError(f"Event #{idx} scale_stations requires 'new_total'")
        if action in {"stop_stations"}:
            if "ids" not in event and "count" not in event:
                raise ValueError(f"Event #{idx} stop_stations requires 'ids' or 'count'")
        if action == "set_price":
            if "value" not in event:
                raise ValueError(f"Event #{idx} set_price requires 'value'")
        if action == "apply_profile":
            for key in ("station_id", "profile_file"):
                if key not in event:
                    raise ValueError(f"Event #{idx} apply_profile requires '{key}'")
        if action == "clear_profile":
            if "station_id" not in event:
                raise ValueError(f"Event #{idx} clear_profile requires 'station_id'")


def _load_profile_file(file_path: str) -> Dict[str, Any]:
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Profile file not found: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_path(path: str, base_dir: Optional[str]) -> str:
    if os.path.isabs(path) or not base_dir:
        return path
    return os.path.normpath(os.path.join(base_dir, path))


def _format_time(seconds: float) -> str:
    total_seconds = int(seconds)
    minutes = total_seconds // 60
    secs = total_seconds % 60
    return f"{minutes:02d}:{secs:02d}"


async def _run_cli(file_path: str, dry_run: bool) -> None:
    runner = ScenarioRunner(dry_run=dry_run)
    events = runner.load_scenario(file_path)
    await runner.run(events, base_dir=os.path.dirname(file_path))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run simulation scenarios.")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Run a scenario file")
    run_parser.add_argument("file_path", help="Path to scenario YAML/JSON")
    run_parser.add_argument("--dry-run", action="store_true", help="Log events without executing")

    args = parser.parse_args()

    if args.command == "run":
        asyncio.run(_run_cli(args.file_path, args.dry_run))


if __name__ == "__main__":
    main()
