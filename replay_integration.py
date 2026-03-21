from __future__ import annotations

import asyncio
import csv
import logging
import os
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable, Deque, Dict, Iterable, Optional, Sequence, Tuple

from csv_loader import assert_no_load_order_metadata, session_uid, load_sessions
from csv_loader import ChargingSession
from fault_injection import FaultScenario
from validation_report import ValidationReport
from validation import validate_sessions
from session_replay_engine import REPLAY_PIPELINE_TOKEN, ReplayConfig, ReplayEvent, SessionReplayEngine
from session_planner import SessionPlan, plan_sessions_from_csv
from replay_clock import ReplayClock, ReplayClockConfig
from charging_loop import (
    ChargingLoop,
    ChargingLoopConfig,
    ChargingLoopState,
    ChargingLoopValidationSnapshot,
)
from profiles import DEFAULT_PROFILES
from state_version import increment_state_version
from accounting import get_session_energy

from replay_mode import (
    assert_real_csv_entry_active,
    assert_replay_mode_explicit,
    begin_real_csv_entry,
    end_real_csv_entry,
    is_real_csv_mode,
    is_real_csv_entry_active,
    is_strict_mode,
    log_mode_banner,
    set_replay_mode,
    ReplayMode,
)
from station import SimulatedChargePoint

_REAL_CSV_TASK: Optional[asyncio.Task] = None
_REAL_CSV_PAUSED: bool = False

logger = logging.getLogger("replay_integration")

_PIPELINE_ACTIVE: bool = False


class _PipelineGuard:
    def __enter__(self) -> None:
        global _PIPELINE_ACTIVE
        if _PIPELINE_ACTIVE:
            raise RuntimeError("Replay pipeline already active")
        _PIPELINE_ACTIVE = True

    def __exit__(self, exc_type, exc, tb) -> None:
        global _PIPELINE_ACTIVE
        _PIPELINE_ACTIVE = False


def build_limit_provider(
    chargepoint_provider: Callable[[str], Optional[SimulatedChargePoint]],
    voltage: Optional[float],
) -> Callable[[ChargingSession, str, datetime], Optional[float]]:
    def provider(session: ChargingSession, connector_id: str, timestamp: datetime) -> Optional[float]:
        station_id = session.data.get("station_id")
        if not isinstance(station_id, str) or not station_id:
            return None
        chargepoint = chargepoint_provider(station_id)
        if chargepoint is None:
            return None

        tx_value = session.data.get("transaction_id")
        transaction_id: Optional[int] = None
        if isinstance(tx_value, int):
            transaction_id = tx_value
        elif isinstance(tx_value, str) and tx_value.strip():
            try:
                transaction_id = int(float(tx_value))
            except ValueError:
                transaction_id = None

        try:
            connector_int = int(float(connector_id))
        except (TypeError, ValueError):
            return None

        start_time = session.data.get("start_time")
        if not isinstance(start_time, datetime):
            return None

        session_voltage = session.data.get("voltage")
        effective_voltage: Optional[float] = None
        if isinstance(session_voltage, (int, float)):
            effective_voltage = float(session_voltage)
        elif isinstance(session_voltage, str) and session_voltage.strip():
            try:
                effective_voltage = float(session_voltage)
            except ValueError:
                effective_voltage = None
        if effective_voltage is None:
            effective_voltage = voltage

        limit = chargepoint.profile_manager.get_limit_watts_at_time(
            connector_id=connector_int,
            target_time=timestamp,
            transaction_start=start_time,
            transaction_id=transaction_id,
            voltage=effective_voltage,
        )

        if chargepoint.profile_manager.has_profiles() and limit is None:
            raise RuntimeError("ChargingProfile exists but no limit applied")

        return limit

    return provider


def create_replay_engine(
    sessions: Sequence[ChargingSession],
    chargepoint_provider: Callable[[str], Optional[SimulatedChargePoint]],
    validation_report: ValidationReport,
    voltage: Optional[float] = None,
    config: Optional[ReplayConfig] = None,
) -> SessionReplayEngine:
    if is_real_csv_mode():
        raise RuntimeError("SessionReplayEngine is forbidden in REAL_CSV mode")
    limit_provider = build_limit_provider(chargepoint_provider, voltage)
    config = config or ReplayConfig(limit_provider=limit_provider)
    config = replace(
        config,
        limit_provider=limit_provider,
        replay_token=REPLAY_PIPELINE_TOKEN,
        validation_report=validation_report,
    )
    return SessionReplayEngine(sessions, config=config)


@dataclass(frozen=True)
class ActiveTransaction:
    station_id: str
    connector_id: int
    transaction_id: int
    session_uid: str


class StationEngineBinder:
    """
    Binds replay events to the station engine public APIs.
    """

    def __init__(
        self,
        chargepoint_provider: Callable[[str], Optional[SimulatedChargePoint]],
        validation_report: ValidationReport,
        expected_sessions: int,
        strict: bool = True,
        fault_scenario: Optional[FaultScenario] = None,
    ) -> None:
        if not _PIPELINE_ACTIVE:
            raise RuntimeError("StationEngineBinder must be constructed via run_replay_pipeline")
        if validation_report is None:
            raise RuntimeError("ValidationReport is required for replay execution")
        if expected_sessions <= 0:
            raise RuntimeError("Expected session count required for validation")
        self._chargepoint_provider = chargepoint_provider
        self._strict = strict
        self._fault_scenario = fault_scenario
        self._validation_report = validation_report
        self._expected_sessions = expected_sessions
        self._validation_finalized = False
        self._active_by_connector: Dict[Tuple[str, int], ActiveTransaction] = {}
        self._session_to_tx: Dict[str, ActiveTransaction] = {}
        self._event_index = 0
        self._pending_events: Deque[Tuple[int, ReplayEvent]] = deque()
        self._energy_state: Dict[str, Dict[str, float]] = {}
        self._session_window: Dict[str, Dict[str, datetime]] = {}
        self._smart_charging_active: Dict[str, bool] = {}

    async def on_start(self, event: ReplayEvent) -> None:
        await self._dispatch_event(event)

    async def on_meter(self, event: ReplayEvent) -> None:
        await self._dispatch_event(event)

    async def on_stop(self, event: ReplayEvent) -> None:
        await self._dispatch_event(event)

    async def _dispatch_event(self, event: ReplayEvent) -> None:
        assert_no_load_order_metadata(event.session)
        event_index = self._event_index
        self._event_index += 1

        if self._fault_scenario is not None:
            self._fault_scenario.update_replay_event_index(event_index)

            if self._fault_scenario.is_disconnect_active(event_index):
                self._pending_events.append((event_index, event))
                return

        await self._flush_pending()
        await self._process_event(event_index, event)

    async def _flush_pending(self) -> None:
        if not self._pending_events:
            return

        pending = list(self._pending_events)
        self._pending_events.clear()
        for index, event in pending:
            await self._process_event(index, event)

    async def _process_event(self, event_index: int, event: ReplayEvent) -> None:
        if event.event_type == "StartTransaction":
            await self._handle_start(event)
            return

        if event.event_type == "MeterValues":
            await self._handle_meter(event_index, event)
            return

        if event.event_type == "StopTransaction":
            await self._handle_stop(event)
            return

        raise ValueError(f"Unsupported replay event type: {event.event_type}")

    async def _handle_start(self, event: ReplayEvent) -> None:
        session_uid_value = _session_uid(event)
        station_id = event.station_id
        connector_id = _require_int(event.connector_id, "connector_id")
        expected_start = _require_datetime(event.session.data.get("start_time"))
        expected_end = _require_datetime(event.session.data.get("end_time"))
        id_tag = _require_text(event.session.data.get("id_tag"), "id_tag")
        meter_start = _require_int(event.session.data.get("meter_start_wh"), "meter_start_wh")

        if session_uid_value in self._energy_state:
            raise ValueError(f"Session {session_uid_value} already started")

        if session_uid_value in self._session_to_tx:
            raise ValueError(f"Session {session_uid_value} already started")

        key = (station_id, connector_id)
        if key in self._active_by_connector:
            msg = f"Connector {station_id}/{connector_id} already active"
            if self._strict:
                raise ValueError(msg)
            logger.warning(msg)
            return

        chargepoint = self._chargepoint_provider(station_id)
        if chargepoint is None:
            raise ValueError(f"Station {station_id} not available")

        transaction_id = await chargepoint.start_replay_transaction(
            connector_id=connector_id,
            id_tag=id_tag,
            meter_start=meter_start,
            timestamp=event.timestamp,
            session_id=session_uid_value,
        )

        active = ActiveTransaction(
            station_id=station_id,
            connector_id=connector_id,
            transaction_id=transaction_id,
            session_uid=session_uid_value,
        )
        self._active_by_connector[key] = active
        self._session_to_tx[session_uid_value] = active

        self._energy_state[session_uid_value] = {
            "last_wh": 0.0,
            "emitted_wh": 0.0,
            "suppressed_wh": 0.0,
            "emitted_count": 0.0,
        }
        self._session_window[session_uid_value] = {
            "expected_start": expected_start,
            "expected_end": expected_end,
            "actual_start": event.timestamp,
        }
        self._smart_charging_active[session_uid_value] = False

    async def _handle_meter(self, event_index: int, event: ReplayEvent) -> None:
        station_id = event.station_id
        connector_id = _require_int(event.connector_id, "connector_id")
        key = (station_id, connector_id)

        active = self._active_by_connector.get(key)
        if active is None:
            raise ValueError(f"No active transaction for {station_id}/{connector_id}")
        energy_wh = _require_float(event.payload.get("energy_wh"), "energy_wh")

        window = self._session_window.get(active.session_uid)
        if window is None:
            raise ValueError(f"Session window missing for session {active.session_uid}")

        expected_start = window["expected_start"]
        expected_end = window["expected_end"]
        smart_charging_active = bool(event.payload.get("smart_charging_active"))
        if event.timestamp < expected_start:
            raise ValueError("MeterValues emitted before session start")
        if event.timestamp > expected_end and not smart_charging_active:
            raise ValueError("MeterValues emitted beyond session end without smart charging")
        self._smart_charging_active[active.session_uid] = smart_charging_active

        state = self._energy_state.get(active.session_uid)
        if state is None:
            raise ValueError(f"Energy state missing for session {active.session_uid}")

        delta = energy_wh - state["last_wh"]
        if delta < 0:
            raise ValueError("Non-monotonic meter values detected")

        if self._fault_scenario is not None and self._fault_scenario.should_drop_meter_values(event_index):
            state["suppressed_wh"] += delta
            state["last_wh"] = energy_wh
            return

        chargepoint = self._chargepoint_provider(station_id)
        if chargepoint is None:
            raise ValueError(f"Station {station_id} not available")

        await chargepoint.emit_replay_meter_values(
            connector_id=connector_id,
            transaction_id=active.transaction_id,
            energy_wh=energy_wh,
            timestamp=event.timestamp,
        )

        state["emitted_wh"] += delta
        state["last_wh"] = energy_wh
        state["emitted_count"] += 1

    async def _handle_stop(self, event: ReplayEvent) -> None:
        session_uid_value = _session_uid(event)
        station_id = event.station_id
        connector_id = _require_int(event.connector_id, "connector_id")
        key = (station_id, connector_id)

        active = self._active_by_connector.get(key)
        if active is None:
            raise ValueError(f"No active transaction for {station_id}/{connector_id}")
        if active.session_uid != session_uid_value:
            raise ValueError(
                f"Session mismatch on stop: expected {active.session_uid}, got {session_uid_value}"
            )

        chargepoint = self._chargepoint_provider(station_id)
        if chargepoint is None:
            raise ValueError(f"Station {station_id} not available")

        total_energy_kwh = _require_float(
            event.session.data.get("total_energy_kwh"),
            "total_energy_kwh",
        )
        interval_seconds = _require_int(
            event.session.data.get("meter_intervals_sec"),
            "meter_intervals_sec",
        )
        meter_stop = int(round(total_energy_kwh * 1000.0))
        id_tag = _require_text(event.session.data.get("id_tag"), "id_tag")

        await chargepoint.stop_replay_transaction(
            connector_id=connector_id,
            transaction_id=active.transaction_id,
            meter_stop=meter_stop,
            timestamp=event.timestamp,
            id_tag=id_tag,
        )

        state = self._energy_state.get(active.session_uid)
        if state is None:
            raise ValueError(f"Energy state missing for session {active.session_uid}")

        window = self._session_window.get(active.session_uid)
        if window is None:
            raise ValueError(f"Session window missing for session {active.session_uid}")

        total_wh = total_energy_kwh * 1000.0
        if abs((state["emitted_wh"] + state["suppressed_wh"]) - total_wh) > 1e-6:
            raise ValueError("Energy accounting mismatch under fault injection")

        report = self._validation_report
        expected_duration = (window["expected_end"] - window["expected_start"]).total_seconds()
        actual_duration = (event.timestamp - window["actual_start"]).total_seconds()
        result = report.record_session(
            session_uid=active.session_uid,
            expected_duration_seconds=expected_duration,
            actual_duration_seconds=actual_duration,
            expected_total_energy_kwh=total_energy_kwh,
            actual_total_energy_kwh=state["emitted_wh"] / 1000.0,
            emitted_meter_values_count=int(state["emitted_count"]),
            smart_charging_active=bool(self._smart_charging_active.get(active.session_uid)),
            reporting_interval_seconds=interval_seconds,
            faults=(
                ["meter_values_dropped"]
                if state["suppressed_wh"] > 0
                else []
            )
            + (["fault_scenario"] if self._fault_scenario is not None else []),
        )
        if not result.passed:
            if self._strict and is_strict_mode():
                raise RuntimeError("Validation failed for session")
            logger.warning("Validation failed for session %s", active.session_uid)
        if len(report.results) > self._expected_sessions:
            raise RuntimeError("Validation recorded more sessions than expected")
        if len(report.results) == self._expected_sessions:
            if self._strict and is_strict_mode():
                report.assert_all_passed()
            self._validation_finalized = True

        self._active_by_connector.pop(key, None)
        self._session_to_tx.pop(session_uid_value, None)
        self._energy_state.pop(session_uid_value, None)
        self._session_window.pop(session_uid_value, None)
        self._smart_charging_active.pop(session_uid_value, None)

    def assert_validation_finalized(self) -> None:
        if not self._validation_finalized:
            raise RuntimeError("Replay completed without finalized validation")


def _resolve_cleaned_csv_paths(
    csv_directory: Optional[str],
    csv_files: Optional[Sequence[str]],
) -> Sequence[Path]:
    if csv_files:
        return [Path(path) for path in csv_files]
    if csv_directory:
        return [Path(csv_directory) / "cleaned_sessions.csv"]
    return [Path.cwd() / "cleaned_sessions.csv"]


def _verify_cleaned_csv(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"Cleaned CSV not found: {path}")
    meta_path = path.with_suffix(".meta")
    if not meta_path.exists():
        raise RuntimeError(f"Cleaned CSV metadata missing: {meta_path}")
    meta_text = meta_path.read_text(encoding="utf-8").strip()
    if meta_text != "CLEANED_CSV_V1":
        raise RuntimeError("Cleaned CSV metadata invalid")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise RuntimeError(f"Cleaned CSV missing header: {path}")
        expected = {"station_id", "start_time", "end_time", "total_energy_kWh"}
        if set(reader.fieldnames) != expected:
            raise RuntimeError(
                "Cleaned CSV header mismatch; expected station_id,start_time,end_time,total_energy_kWh"
            )


def _policy_limit_kw_provider(
    chargepoint: SimulatedChargePoint,
    connector_id: int,
    transaction_start: datetime,
    transaction_id: Optional[int],
    voltage: Optional[float],
) -> Callable[[datetime, ChargingLoopState], Optional[Decimal]]:
    def provider(replay_time: datetime, state: ChargingLoopState) -> Optional[Decimal]:
        limit_w = chargepoint.profile_manager.get_limit_watts_at_time(
            connector_id=connector_id,
            target_time=replay_time,
            transaction_start=transaction_start,
            transaction_id=transaction_id,
            voltage=voltage,
        )
        if chargepoint.profile_manager.has_profiles() and limit_w is None:
            raise RuntimeError("ChargingProfile exists but no limit applied")
        if limit_w is None:
            return None
        return Decimal(str(limit_w)) / Decimal("1000")

    return provider


async def run_replay_pipeline(
    *,
    chargepoint_provider: Callable[[str], Optional[SimulatedChargePoint]],
    expected_duration_tolerance_seconds: float,
    expected_energy_tolerance_kwh: float,
    csv_directory: Optional[str] = None,
    csv_files: Optional[Sequence[str]] = None,
    timezone_name: str = "UTC",
    voltage: Optional[float],
    fault_scenario: Optional[FaultScenario] = None,
    strict: bool = True,
) -> ValidationReport:
    with _PipelineGuard():
        assert_replay_mode_explicit()
        log_mode_banner(logger)
        if is_real_csv_mode():
            raise RuntimeError("REAL_CSV must use run_real_csv_replay")
        if is_strict_mode() and not strict:
            raise RuntimeError("STRICT replay validation cannot be weakened")
        if is_real_csv_mode() and strict:
            raise RuntimeError("REAL_CSV replay requires strict=False")
        if is_real_csv_mode():
            strict = False
        else:
            strict = True
        if is_strict_mode():
            if is_real_csv_entry_active():
                raise RuntimeError("STRICT mode forbids REAL_CSV entry")
        sessions = load_sessions(directory=csv_directory, files=csv_files, timezone_name=timezone_name)
        safe_sessions, failures = validate_sessions(sessions, report_path=None)
        if failures and is_strict_mode():
            raise RuntimeError("CSV validation failed; replay aborted")
        if failures and is_real_csv_mode():
            # REAL_CSV TOLERANCE — DO NOT COPY INTO STRICT MODE
            logger.warning("REAL_CSV validation dropped %s session(s)", len(failures))

        validation_report = ValidationReport(
            duration_tolerance_seconds=expected_duration_tolerance_seconds,
            energy_tolerance_kwh=expected_energy_tolerance_kwh,
        )

        engine_config = None
        if is_real_csv_mode():
            engine_config = ReplayConfig(limit_provider=lambda *_: None, strict=strict)

        engine = create_replay_engine(
            safe_sessions,
            chargepoint_provider=chargepoint_provider,
            validation_report=validation_report,
            config=engine_config,
            voltage=voltage,
        )
        binder = StationEngineBinder(
            chargepoint_provider=chargepoint_provider,
            validation_report=validation_report,
            expected_sessions=len(safe_sessions),
            strict=strict,
            fault_scenario=fault_scenario,
        )
        await engine.replay(binder.on_start, binder.on_meter, binder.on_stop)
        binder.assert_validation_finalized()
        return validation_report


async def run_real_csv_replay(
    *,
    chargepoint_provider: Callable[[str], Optional[SimulatedChargePoint]],
    expected_duration_tolerance_seconds: float,
    expected_energy_tolerance_kwh: float,
    csv_directory: Optional[str] = None,
    csv_files: Optional[Sequence[str]] = None,
    timezone_name: str = "UTC",
    voltage: Optional[float],
    fault_scenario: Optional[FaultScenario] = None,
) -> ValidationReport:
    global _REAL_CSV_TASK
    global _REAL_CSV_PAUSED
    if _REAL_CSV_TASK is not None:
        if not _REAL_CSV_TASK.done():
            raise RuntimeError("REAL_CSV replay already active")
        _REAL_CSV_TASK = None
    if is_real_csv_entry_active():
        end_real_csv_entry()
    set_replay_mode(ReplayMode.REAL_CSV)
    log_mode_banner(logger)
    logger.warning("REAL-CSV MODE ENABLED — TOLERANT REPLAY")
    logger.warning("REAL_CSV MODE IS NOT FOR CORRECTNESS VALIDATION")
    logger.warning("RESULTS MAY DIFFER FROM STRICT MODE BY DESIGN")
    if os.getenv("CI"):
        logger.warning("REAL-CSV mode used under CI")
    begin_real_csv_entry()
    _REAL_CSV_TASK = asyncio.current_task()
    _REAL_CSV_PAUSED = False
    try:
        paths = _resolve_cleaned_csv_paths(csv_directory, csv_files)
        for path in paths:
            _verify_cleaned_csv(path)

        plans: list[SessionPlan] = []
        for path in paths:
            plans.extend(plan_sessions_from_csv(path))
        if not plans:
            raise RuntimeError("No SessionPlan rows loaded from cleaned CSV")

        validation_report = ValidationReport(
            duration_tolerance_seconds=expected_duration_tolerance_seconds,
            energy_tolerance_kwh=expected_energy_tolerance_kwh,
        )

        start_time = min(plan.start_time for plan in plans)
        end_time = max(plan.end_time for plan in plans)
        clock = ReplayClock(
            start_time,
            end_time,
            config=ReplayClockConfig(acceleration=1.0),
        )
        clock.start()

        loops: list[ChargingLoop] = []
        for plan in sorted(plans, key=lambda p: (p.start_time, p.station_id)):
            chargepoint = chargepoint_provider(plan.station_id)
            if chargepoint is None:
                raise RuntimeError(f"Station {plan.station_id} not available")

            profile = DEFAULT_PROFILES.get("default")
            if profile is None:
                raise RuntimeError("Default profile missing")

            chargepoint.update_battery_profile(
                capacity_kwh=profile.battery_capacity_kwh,
                soc_kwh=profile.initial_soc_kwh,
                temperature_c=profile.temperature_c,
                max_charge_power_kw=profile.max_charge_power_kw,
                tapering_enabled=profile.tapering_enabled,
            )

            plan_for_loop = SessionPlan(
                station_id=plan.station_id,
                start_time=plan.start_time,
                end_time=plan.end_time,
                duration_sec=plan.duration_sec,
                total_energy_kWh=plan.total_energy_kWh,
                avg_power_kW=Decimal(str(profile.max_charge_power_kw)),
            )

            connector_id = 1
            policy_limit_provider = _policy_limit_kw_provider(
                chargepoint,
                connector_id=connector_id,
                transaction_start=plan_for_loop.start_time,
                transaction_id=None,
                voltage=voltage,
            )
            policy_id = "ocpp_profile" if chargepoint.profile_manager.has_profiles() else None

            loop = ChargingLoop(
                plan_for_loop,
                chargepoint,
                config=ChargingLoopConfig(
                    tick_interval_sec=1.0,
                    connector_id=connector_id,
                    id_tag=plan.station_id,
                    policy_id=policy_id,
                    policy_params={
                        "voltage": voltage,
                        "profile": profile.name,
                        "battery_capacity_kwh": profile.battery_capacity_kwh,
                        "initial_soc_kwh": profile.initial_soc_kwh,
                        "temperature_c": profile.temperature_c,
                        "max_charge_power_kw": profile.max_charge_power_kw,
                        "tapering_enabled": profile.tapering_enabled,
                        "charge_if_price_below": profile.charge_if_price_below,
                        "max_energy_kwh": profile.max_energy_kwh,
                        "allow_peak": profile.allow_peak,
                        "peak_hours": profile.peak_hours,
                    },
                    battery_capacity_kwh=Decimal(str(profile.battery_capacity_kwh)),
                    initial_soc_kwh=Decimal(str(profile.initial_soc_kwh)),
                    temperature_c=Decimal(str(profile.temperature_c)),
                    max_charge_power_kw=Decimal(str(profile.max_charge_power_kw)),
                    tapering_enabled=bool(profile.tapering_enabled),
                    policy_limit_kw_provider=policy_limit_provider,
                ),
            )
            loops.append(loop)

        tick_interval = clock.tick_seconds
        active = set(loops)
        completed: list[ChargingLoop] = []

        while active:
            await asyncio.sleep(tick_interval)
            if _REAL_CSV_PAUSED:
                if clock.running:
                    clock.stop()
                continue
            if not clock.running:
                clock.start()
            replay_time = clock.tick()
            increment_state_version()

            for loop in list(active):
                done = await loop.on_tick(replay_time)
                if done:
                    active.remove(loop)
                    completed.append(loop)

        for loop in completed:
            snapshot: ChargingLoopValidationSnapshot = loop.validation_snapshot()
            actual_energy = get_session_energy(snapshot["session_uid"])
            if actual_energy is None:
                raise RuntimeError(
                    f"Session energy missing for {snapshot['session_uid']}"
                )
            validation_report.record_session(
                session_uid=snapshot["session_uid"],
                expected_duration_seconds=snapshot["expected_duration_seconds"],
                actual_duration_seconds=snapshot["actual_duration_seconds"],
                expected_total_energy_kwh=snapshot["expected_total_energy_kwh"],
                actual_total_energy_kwh=float(actual_energy),
                emitted_meter_values_count=snapshot["emitted_meter_values_count"],
                smart_charging_active=snapshot["smart_charging_active"],
                reporting_interval_seconds=int(tick_interval),
                faults=[],
            )

        validation_report.finalize_with_warnings()
        return validation_report
    finally:
        _REAL_CSV_TASK = None
        end_real_csv_entry()


def _session_uid(event: ReplayEvent) -> str:
    return session_uid(event.session)


def pause_real_csv_replay() -> None:
    global _REAL_CSV_PAUSED
    if _REAL_CSV_TASK is None or _REAL_CSV_TASK.done():
        raise RuntimeError("REAL_CSV replay not active")
    _REAL_CSV_PAUSED = True


def resume_real_csv_replay() -> None:
    global _REAL_CSV_PAUSED
    if _REAL_CSV_TASK is None or _REAL_CSV_TASK.done():
        raise RuntimeError("REAL_CSV replay not active")
    _REAL_CSV_PAUSED = False


def is_real_csv_replay_paused() -> bool:
    return _REAL_CSV_PAUSED


def _require_text(value: object, field: str) -> str:
    if isinstance(value, str) and value:
        return value
    raise ValueError(f"Required field '{field}' missing or invalid")


def _require_int(value: object, field: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value))
        except ValueError as exc:
            raise ValueError(f"Required integer field '{field}' invalid") from exc
    if isinstance(value, float):
        return int(value)
    raise ValueError(f"Required integer field '{field}' missing or invalid")


def _require_float(value: object, field: str) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"Required numeric field '{field}' invalid") from exc
    raise ValueError(f"Required numeric field '{field}' missing or invalid")


def _require_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    raise ValueError("Required datetime field missing or invalid")
