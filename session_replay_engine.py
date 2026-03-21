from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple, Union

from csv_loader import ChargingSession, session_uid
from determinism_guards import assert_no_fallback_defaults, assert_no_randomness
from meter_values_generator import generate_meter_values
from replay_mode import (
    assert_real_csv_entry_active,
    assert_replay_mode_explicit,
    is_real_csv_mode,
    is_strict_mode,
)
from validation_report import ValidationReport


logger = logging.getLogger("session_replay")


@dataclass(frozen=True)
class ReplayEvent:
    """Deterministic event emitted to the station engine."""

    timestamp: datetime
    event_type: str
    station_id: str
    connector_id: str
    session: ChargingSession
    payload: Dict[str, Any]


OrderingKey = Union[
    Tuple[datetime, str, datetime, float],
    Tuple[datetime, str, datetime, float, str],
]


@dataclass(frozen=True)
class EventEnvelope:
    ordering_key: OrderingKey
    event: ReplayEvent


REPLAY_PIPELINE_TOKEN = object()


@dataclass(frozen=True)
class ReplayConfig:
    """Configuration for replay timing and ordering."""

    limit_provider: Callable[[ChargingSession, str, datetime], Optional[float]]
    time_scale: float = 1.0
    strict: bool = True
    replay_token: Optional[object] = None
    validation_report: Optional[ValidationReport] = None


StartCallback = Callable[[ReplayEvent], Optional[Awaitable[None]]]
MeterCallback = Callable[[ReplayEvent], Optional[Awaitable[None]]]
StopCallback = Callable[[ReplayEvent], Optional[Awaitable[None]]]


class SessionReplayEngine:
    """
    Deterministic session replay engine.

    Produces time-ordered lifecycle events and emits them to the station engine
    via callbacks. Does not handle OCPP or transport.
    """

    def __init__(
        self,
        sessions: Sequence[ChargingSession],
        config: ReplayConfig,
    ) -> None:
        if is_real_csv_mode():
            raise RuntimeError("SessionReplayEngine is forbidden in REAL_CSV mode")
        if is_strict_mode():
            assert_no_randomness()
            assert_no_fallback_defaults(sessions)
        self._sessions = list(sessions)
        self._config = config
        if self._config.replay_token is not REPLAY_PIPELINE_TOKEN:
            raise RuntimeError("Replay must be constructed via create_replay_engine")
        if self._config.limit_provider is None:
            raise RuntimeError("limit_provider required for deterministic replay")
        self._schedule: List[EventEnvelope] = []
        self._validation_finalized = False

    def build_schedule(self) -> List[EventEnvelope]:
        """
        Build a deterministic event schedule sorted by session start time.
        """
        assert_replay_mode_explicit()
        sessions = sorted(self._sessions, key=_ordering_key)

        for session in sessions:
            _assert_ordering_key(_ordering_key(session))

        _assert_unique_replay_keys(sessions)

        last_end_by_connector: Dict[Tuple[str, str], datetime] = {}
        last_end_by_station: Dict[str, datetime] = {}
        events_by_key: Dict[OrderingKey, List[ReplayEvent]] = {}

        for session in sessions:
            start_time = _require_datetime(session.data.get("start_time"))
            end_time = _require_datetime(session.data.get("end_time"))
            station_id = _require_text(session.data.get("station_id"), "station_id")
            ordering_key = _ordering_key(session)
            _assert_ordering_key(ordering_key)
            connector_id = _require_connector_id(session)

            if is_real_csv_mode() and not self._config.strict:
                # REAL_CSV TOLERANCE — DO NOT COPY INTO STRICT MODE
                station_last_end = last_end_by_station.get(station_id)
                if station_last_end and start_time < station_last_end:
                    duration_seconds = max(0.0, (end_time - start_time).total_seconds())
                    start_time = station_last_end
                    end_time = station_last_end + timedelta(seconds=duration_seconds)
                    logger.warning(
                        "REAL_CSV overlap shift for %s: start->%s end->%s",
                        station_id,
                        start_time.isoformat(),
                        end_time.isoformat(),
                    )
                    adjusted = dict(session.data)
                    adjusted["start_time"] = start_time
                    adjusted["end_time"] = end_time
                    session = ChargingSession(data=MappingProxyType(adjusted))
                    ordering_key = _ordering_key(session)
                    _assert_ordering_key(ordering_key)

            connector_key = (station_id, connector_id)
            last_end = last_end_by_connector.get(connector_key)
            if last_end and start_time < last_end:
                msg = (
                    f"Overlapping sessions on connector {connector_key}: "
                    f"start={start_time.isoformat()} < last_end={last_end.isoformat()}"
                )
                raise ValueError(msg)

            total_energy_kwh = _require_float(
                session.data.get("total_energy_kwh"),
                "total_energy_kwh",
            )
            interval_seconds = _require_int(
                session.data.get("meter_intervals_sec"),
                "meter_intervals_sec",
            )
            meter_series, smart_charging_active, extended_session = _generate_meter_series(
                start_time,
                end_time,
                total_energy_kwh,
                interval_seconds,
                self._config.limit_provider,
                session=session,
                connector_id=connector_id,
            )
            if is_strict_mode():
                if smart_charging_active:
                    raise RuntimeError("STRICT mode forbids smart-charging-driven timing")
                if extended_session:
                    raise RuntimeError("STRICT mode forbids session extension")
                if len(meter_series) != 1:
                    raise RuntimeError(
                        "STRICT mode requires exactly one MeterValues event per session"
                    )
            if extended_session and not smart_charging_active:
                raise RuntimeError("Session extension without smart charging is forbidden")

            events_by_key.setdefault(ordering_key, []).append(
                ReplayEvent(
                    timestamp=start_time,
                    event_type="StartTransaction",
                    station_id=station_id,
                    connector_id=connector_id,
                    session=session,
                    payload={
                        "smart_charging_active": smart_charging_active,
                        "extended_session": extended_session,
                    },
                )
            )

            for ts, value in meter_series:
                events_by_key.setdefault(ordering_key, []).append(
                    ReplayEvent(
                        timestamp=ts,
                        event_type="MeterValues",
                        station_id=station_id,
                        connector_id=connector_id,
                        session=session,
                        payload={
                            "energy_wh": value,
                            "smart_charging_active": smart_charging_active,
                            "extended_session": extended_session,
                        },
                    )
                )

            stop_time = end_time
            if meter_series:
                last_ts = meter_series[-1][0]
                if last_ts > stop_time:
                    stop_time = last_ts

            events_by_key.setdefault(ordering_key, []).append(
                ReplayEvent(
                    timestamp=stop_time,
                    event_type="StopTransaction",
                    station_id=station_id,
                    connector_id=connector_id,
                    session=session,
                    payload={
                        "smart_charging_active": smart_charging_active,
                        "extended_session": extended_session,
                    },
                )
            )

            last_end_by_connector[connector_key] = end_time
            if is_real_csv_mode() and not self._config.strict:
                last_end_by_station[station_id] = end_time

        envelopes: List[EventEnvelope] = []
        for key in sorted(events_by_key.keys()):
            _assert_ordering_key(key)
            events = events_by_key[key]
            start_events = [event for event in events if event.event_type == "StartTransaction"]
            stop_events = [event for event in events if event.event_type == "StopTransaction"]
            meter_events = [event for event in events if event.event_type == "MeterValues"]

            if len(start_events) != 1 or len(stop_events) != 1:
                raise ValueError("Replay sequencing requires exactly one start and one stop per session")

            meter_events = sorted(meter_events, key=lambda e: e.timestamp)
            ordered = [start_events[0], *meter_events, stop_events[0]]
            for event in ordered:
                envelopes.append(EventEnvelope(ordering_key=key, event=event))

        self._schedule = list(envelopes)
        return list(self._schedule)

    async def replay(
        self,
        on_start: StartCallback,
        on_meter: MeterCallback,
        on_stop: StopCallback,
    ) -> None:
        """
        Replay scheduled events in deterministic order.
        """
        if is_real_csv_mode():
            assert_real_csv_entry_active()
        binder = getattr(on_start, "__self__", None)
        if binder is None or not hasattr(binder, "assert_validation_finalized"):
            raise RuntimeError("Replay must be executed via StationEngineBinder")
        if getattr(on_meter, "__self__", None) is not binder or getattr(on_stop, "__self__", None) is not binder:
            raise RuntimeError("Replay callbacks must be bound to StationEngineBinder")
        if self._config.validation_report is None:
            raise RuntimeError("ValidationReport required for replay")
        schedule = self._schedule or self.build_schedule()
        if not schedule:
            self.finalize_and_validate()
            if not self._validation_finalized:
                raise RuntimeError("Replay finalized without validation")
            return

        for envelope in schedule:
            _assert_ordering_key(envelope.ordering_key)
            event = envelope.event
            if event.payload.get("smart_charging_active") and self._config.limit_provider is None:
                raise RuntimeError("ChargingProfile active without limit provider")
            if event.event_type == "StartTransaction":
                await _maybe_await(on_start, event)
            elif event.event_type == "MeterValues":
                await _maybe_await(on_meter, event)
            elif event.event_type == "StopTransaction":
                await _maybe_await(on_stop, event)

        self.finalize_and_validate()
        if not self._validation_finalized:
            raise RuntimeError("Replay finalized without validation")

    def finalize_and_validate(self) -> None:
        assert_replay_mode_explicit()
        report = self._config.validation_report
        if report is None:
            raise RuntimeError("ValidationReport required for replay")
        if is_real_csv_mode() and not self._config.strict:
            # REAL_CSV TOLERANCE — DO NOT COPY INTO STRICT MODE
            report.finalize_with_warnings()
            self._validation_finalized = True
            return
        report.finalize()
        self._validation_finalized = True


def _require_connector_id(session: ChargingSession) -> str:
    value = session.data.get("connector_id")
    if isinstance(value, str) and value:
        return value
    if isinstance(value, (int, float)):
        return str(int(value))
    raise ValueError("CSV-driven connector_id required")


def _require_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    raise ValueError("Required datetime field missing or invalid")


def _require_text(value: Any, field: str) -> str:
    if isinstance(value, str) and value:
        return value
    raise ValueError(f"Required field '{field}' missing or invalid")


def _require_float(value: Any, field: str) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"Required numeric field '{field}' invalid") from exc
    raise ValueError(f"Required numeric field '{field}' missing or invalid")


def _require_int(value: Any, field: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value))
        except ValueError as exc:
            raise ValueError(f"Required integer field '{field}' invalid") from exc
    raise ValueError(f"Required integer field '{field}' missing or invalid")


def _base_ordering_key(session: ChargingSession) -> Tuple[datetime, str, datetime, float]:
    return (
        _require_datetime(session.data.get("start_time")),
        _require_text(session.data.get("station_id"), "station_id"),
        _require_datetime(session.data.get("end_time")),
        _require_float(session.data.get("total_energy_kwh"), "total_energy_kwh"),
    )


def _tie_breaker(session: ChargingSession) -> str:
    candidates = (
        ("booking_id", session.data.get("booking_id")),
        ("transaction_id", session.data.get("transaction_id")),
        ("id_tag_source", session.data.get("id_tag_source")),
        ("id_tag", session.data.get("id_tag")),
        ("meter_start_wh", session.data.get("meter_start_wh")),
        ("connector_id", session.data.get("connector_id")),
    )
    for name, value in candidates:
        if isinstance(value, str) and value.strip():
            return f"{name}:{value.strip()}"
        if isinstance(value, (int, float)):
            return f"{name}:{int(value)}"
    return f"session_uid:{session_uid(session)}"


def _ordering_key(session: ChargingSession) -> OrderingKey:
    assert_replay_mode_explicit()
    base = _base_ordering_key(session)
    if is_real_csv_mode():
        return (*base, _tie_breaker(session))
    return base


def _assert_ordering_key(key: OrderingKey) -> None:
    if is_real_csv_mode():
        if len(key) != 5:
            raise AssertionError("Replay ordering key must have exactly 5 elements")
        start_time, station_id, end_time, total_energy_kwh, unique_id = key
    else:
        if len(key) != 4:
            raise AssertionError("Replay ordering key must have exactly 4 elements")
        start_time, station_id, end_time, total_energy_kwh = key
        unique_id = ""
    if not isinstance(start_time, datetime):
        raise AssertionError("Replay ordering key[0] must be start_time datetime")
    if not isinstance(station_id, str) or not station_id:
        raise AssertionError("Replay ordering key[1] must be station_id string")
    if not isinstance(end_time, datetime):
        raise AssertionError("Replay ordering key[2] must be end_time datetime")
    if not isinstance(total_energy_kwh, float):
        raise AssertionError("Replay ordering key[3] must be total_energy_kwh float")
    if is_real_csv_mode():
        if not isinstance(unique_id, str) or not unique_id:
            raise AssertionError("Replay ordering key[4] must be unique session id")


def _generate_meter_series(
    start_time: datetime,
    end_time: datetime,
    total_energy_kwh: float,
    reporting_interval_seconds: int,
    limit_provider: Callable[[ChargingSession, str, datetime], Optional[float]],
    session: Optional[ChargingSession] = None,
    connector_id: Optional[str] = None,
) -> Tuple[List[Tuple[datetime, float]], bool, bool]:
    limit_fn = None
    if limit_provider is not None and session is not None and connector_id is not None:
        def _limit_fn(ts: datetime) -> Optional[float]:
            return limit_provider(session, connector_id, ts)
        limit_fn = _limit_fn

    series = generate_meter_values(
        start_time=start_time,
        end_time=end_time,
        total_energy_kwh=total_energy_kwh,
        reporting_interval_seconds=reporting_interval_seconds,
        limit_watts_provider=limit_fn,
    )
    smart_charging_active = False
    if limit_provider is not None and session is not None and connector_id is not None:
        for ts in series.timestamps:
            if limit_provider(session, connector_id, ts) is not None:
                smart_charging_active = True
                break

    extended_session = False
    if series.timestamps:
        extended_session = series.timestamps[-1] > end_time

    return list(zip(series.timestamps, series.values_wh)), smart_charging_active, extended_session


def _assert_unique_replay_keys(sessions: Sequence[ChargingSession]) -> None:
    base_keys = [_base_ordering_key(session) for session in sessions]
    if len(set(base_keys)) != len(base_keys):
        if is_strict_mode():
            raise ValueError(
                "Replay ordering ambiguous: duplicate session keys detected for "
                "(start_time, station_id, end_time, total_energy_kwh)"
            )

    keys = [_ordering_key(session) for session in sessions]
    if len(set(keys)) != len(keys):
        raise ValueError(
            "Replay ordering ambiguous: duplicate session keys detected for "
            "(start_time, station_id, end_time, total_energy_kwh)"
            + (", tie_breaker" if is_real_csv_mode() else "")
        )


async def _maybe_await(callback: Callable[[ReplayEvent], Optional[Awaitable[None]]], event: ReplayEvent) -> None:
    result = callback(event)
    if asyncio.iscoroutine(result):
        await result
