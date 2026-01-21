import argparse
import asyncio
import contextlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, cast

import websockets
from ocpp.v16 import ChargePoint as CP
from ocpp.v16 import call

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

from controller_api import CSMS_URL, StationManager
from profiles import DEFAULT_PROFILES
import controller_api
from fault_injector import FaultRule, FaultType, fault_manager
from security_monitor import EventType, security_monitor


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scenario_engine")

SUPPORTED_ACTIONS = {
    "start_stations",
    "stop_stations",
    "set_price",
    "apply_profile",
    "clear_profile",
    "scale_stations",
    "inject_fault",
    "spoof_command",
    "tamper_payload",
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
                    await self.manager.scale_to(0, count, profile)

            elif action == "scale_stations":
                new_total = int(event["new_total"])
                profile = event.get("profile", "default")
                self._log(f"[{timestamp}] Scale stations total={new_total} profile={profile}")
                if not self.dry_run:
                    await self.manager.scale_to(0, new_total, profile)

            elif action == "stop_stations":
                ids = event.get("ids")
                count = event.get("count")
                if ids:
                    self._log(f"[{timestamp}] Stop stations ids={ids}")
                    if not self.dry_run:
                        for station_id in ids:
                            await self.manager.stop_station(0, station_id)
                else:
                    if count is None:
                        raise ValueError("stop_stations requires 'count' when ids not provided")
                    count = int(count)
                    self._log(f"[{timestamp}] Stop stations count={count}")
                    if not self.dry_run:
                        active_ids = list(self.manager.tasks.keys())
                        for station_id in active_ids[:count]:
                            await self.manager.stop_station(0, station_id)

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
                    chargepoint = cast(Any, self.manager.station_chargepoints.get(station_id))
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
                    chargepoint = cast(Any, self.manager.station_chargepoints.get(station_id))
                    if not chargepoint:
                        raise ValueError(f"Station {station_id} not connected")
                    await chargepoint.clear_charging_profile_from_station()

            elif action == "inject_fault":
                station_id = event["station_id"]
                fault_type_value = event["type"]
                duration = event.get("duration")
                message_type = event.get("message_type")
                self._log(
                    f"[{timestamp}] Inject fault {fault_type_value} station_id={station_id} "
                    f"duration={duration} message_type={message_type}"
                )
                if not self.dry_run:
                    if fault_type_value == "HEARTBEAT_FLOOD":
                        chargepoint = cast(Any, self.manager.station_chargepoints.get(station_id))
                        if chargepoint:
                            chargepoint.enable_heartbeat_flood(duration=duration)
                        security_monitor.log_event(
                            EventType.HEARTBEAT_FLOOD,
                            station_id,
                            f"Heartbeat flood injected for {duration}s",
                            severity="medium",
                        )
                    elif fault_type_value == "DUPLICATE_TRANSACTIONS":
                        chargepoint = cast(Any, self.manager.station_chargepoints.get(station_id))
                        if chargepoint:
                            chargepoint.enable_duplicate_transactions(duration=duration)
                        security_monitor.log_event(
                            EventType.DUPLICATE_TRANSACTION,
                            station_id,
                            f"Duplicate transaction IDs injected for {duration}s",
                            severity="medium",
                        )
                    else:
                        fault_type = FaultType(fault_type_value)
                        fault_manager.add_fault_rule(
                            FaultRule(
                                fault_type=fault_type,
                                station_id=station_id,
                                trigger_time=0,
                                duration=duration,
                                message_type=message_type,
                            )
                        )

            elif action == "spoof_command":
                station_id = event["station_id"]
                message_type = event.get("type", "BootNotification")
                payload = event.get("payload", {})
                self._log(
                    f"[{timestamp}] Spoof command type={message_type} station_id={station_id}"
                )
                if not self.dry_run:
                    await _send_spoofed_command(
                        station_id=station_id,
                        message_type=message_type,
                        payload=payload,
                        csms_url=self.manager.csms_url,
                    )
                    security_monitor.log_event(
                        EventType.UNAUTHORIZED_COMMAND,
                        station_id,
                        f"Spoofed {message_type} command sent",
                        severity="high",
                    )

            elif action == "tamper_payload":
                station_id = event["station_id"]
                target_message = event.get("target_message")
                corruption_type = event.get("corruption_type", "truncate_field")
                duration = event.get("duration")
                self._log(
                    f"[{timestamp}] Tamper payload station_id={station_id} target={target_message} "
                    f"corruption={corruption_type} duration={duration}"
                )
                if not self.dry_run:
                    chargepoint = cast(Any, self.manager.station_chargepoints.get(station_id))
                    if chargepoint:
                        chargepoint.enable_tamper_payload(
                            target_message=target_message,
                            corruption_type=corruption_type,
                            duration=duration,
                        )
                    else:
                        await _send_malformed_payload(
                            station_id=station_id,
                            csms_url=self.manager.csms_url,
                        )
                    security_monitor.log_event(
                        EventType.MALFORMED_MESSAGE,
                        station_id,
                        f"Tamper payload injected ({corruption_type})",
                        severity="high",
                    )

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
        if action == "inject_fault":
            for key in ("station_id", "type"):
                if key not in event:
                    raise ValueError(f"Event #{idx} inject_fault requires '{key}'")
        if action == "spoof_command":
            for key in ("station_id", "type"):
                if key not in event:
                    raise ValueError(f"Event #{idx} spoof_command requires '{key}'")
        if action == "tamper_payload":
            if "station_id" not in event:
                raise ValueError(f"Event #{idx} tamper_payload requires 'station_id'")


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


async def _send_spoofed_command(
    station_id: str,
    message_type: str,
    payload: Dict[str, Any],
    csms_url: str,
) -> None:
    ws = await websockets.connect(
        f"{csms_url}/{station_id}",
        subprotocols=cast(Any, ["ocpp1.6"]),
    )
    cp = CP(station_id, ws)
    recv_task = asyncio.create_task(cp.start())
    try:
        if message_type == "BootNotification":
            req = call.BootNotification(
                charge_point_model=payload.get("charge_point_model", "Spoofed-Model"),
                charge_point_vendor=payload.get("charge_point_vendor", "Spoofed-Vendor"),
            )
        elif message_type == "Heartbeat":
            req = call.Heartbeat()
        elif message_type == "Authorize":
            req = call.Authorize(id_tag=payload.get("id_tag", "SPOOF"))
        elif message_type == "StartTransaction":
            req = call.StartTransaction(
                connector_id=payload.get("connector_id", 1),
                id_tag=payload.get("id_tag", "SPOOF"),
                meter_start=payload.get("meter_start", 0),
                timestamp=payload.get("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ")),
            )
        elif message_type == "MeterValues":
            req = call.MeterValues(
                connector_id=payload.get("connector_id", 1),
                transaction_id=payload.get("transaction_id", 9999),
                meter_value=payload.get("meter_value", []),
            )
        elif message_type == "StopTransaction":
            req = call.StopTransaction(
                transaction_id=payload.get("transaction_id", 9999),
                meter_stop=payload.get("meter_stop", 0),
                timestamp=payload.get("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ")),
                id_tag=payload.get("id_tag"),
            )
        else:
            raise ValueError(f"Unsupported spoof_command type: {message_type}")
        await cp.call(req)
    except Exception as exc:
        logger.warning("Spoofed command failed: %s", exc)
    finally:
        recv_task.cancel()
        with contextlib.suppress(Exception):
            await ws.close()


async def _send_malformed_payload(station_id: str, csms_url: str) -> None:
    ws = await websockets.connect(
        f"{csms_url}/{station_id}",
        subprotocols=cast(Any, ["ocpp1.6"]),
    )
    try:
        await ws.send("{bad_json:")
    except Exception as exc:
        logger.warning("Malformed payload send failed: %s", exc)
    finally:
        await ws.close()


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
