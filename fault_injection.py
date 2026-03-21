from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set, Tuple


@dataclass(frozen=True)
class DisconnectWindow:
    start_event_index: int
    duration_events: int

    def contains(self, event_index: int) -> bool:
        return self.start_event_index <= event_index < self.start_event_index + self.duration_events


@dataclass(frozen=True)
class DropMeterValues:
    indices: Set[int]

    def contains(self, event_index: int) -> bool:
        return event_index in self.indices


@dataclass(frozen=True)
class HeartbeatLoss:
    start_event_index: int
    duration_events: int

    def contains(self, event_index: int) -> bool:
        return self.start_event_index <= event_index < self.start_event_index + self.duration_events


@dataclass
class FaultScenario:
    disconnects: List[DisconnectWindow] = field(default_factory=list)
    drop_meter_values: List[DropMeterValues] = field(default_factory=list)
    heartbeat_losses: List[HeartbeatLoss] = field(default_factory=list)
    _last_replay_event_index: Optional[int] = None

    def update_replay_event_index(self, event_index: int) -> None:
        self._last_replay_event_index = event_index

    def is_disconnect_active(self, event_index: int) -> bool:
        return any(window.contains(event_index) for window in self.disconnects)

    def should_drop_meter_values(self, event_index: int) -> bool:
        return any(rule.contains(event_index) for rule in self.drop_meter_values)

    def should_suppress_heartbeat(self) -> bool:
        if self._last_replay_event_index is None:
            return False
        return any(
            window.contains(self._last_replay_event_index)
            for window in self.heartbeat_losses
        )


def websocket_disconnect_at_event(index: int, duration_events: int = 1) -> DisconnectWindow:
    return DisconnectWindow(start_event_index=index, duration_events=duration_events)


def drop_meter_values_at_indices(indices: Iterable[int]) -> DropMeterValues:
    return DropMeterValues(indices=set(indices))


def heartbeat_loss_for_events(start_index: int, duration_events: int) -> HeartbeatLoss:
    return HeartbeatLoss(start_event_index=start_index, duration_events=duration_events)


_ACTIVE_SCENARIO: Optional[FaultScenario] = None


def set_active_fault_scenario(scenario: Optional[FaultScenario]) -> None:
    global _ACTIVE_SCENARIO
    _ACTIVE_SCENARIO = scenario


def get_active_fault_scenario() -> Optional[FaultScenario]:
    return _ACTIVE_SCENARIO


def inject_at_replay_event(event_index: int, duration_events: int = 1) -> FaultScenario:
    if not isinstance(event_index, int) or event_index < 0:
        raise RuntimeError("Replay event index required for fault injection")
    scenario = FaultScenario(disconnects=[websocket_disconnect_at_event(event_index, duration_events)])
    set_active_fault_scenario(scenario)
    return scenario


def inject_for_session_uid(session_uid: str) -> FaultScenario:
    if not isinstance(session_uid, str) or not session_uid:
        raise RuntimeError("Session UID required for fault injection")
    scenario = FaultScenario()
    set_active_fault_scenario(scenario)
    return scenario
