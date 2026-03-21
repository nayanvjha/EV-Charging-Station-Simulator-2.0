import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

logger = logging.getLogger("fault_injector")


class FaultType(str, Enum):
    DISCONNECT = "DISCONNECT"
    TIMEOUT = "TIMEOUT"
    DROP_MESSAGE = "DROP_MESSAGE"
    CORRUPT_PAYLOAD = "CORRUPT_PAYLOAD"


@dataclass
class FaultRule:
    fault_type: FaultType
    station_id: str
    replay_event_index: Optional[int] = None
    session_uid: Optional[str] = None
    replay_counter: Optional[int] = None
    message_type: Optional[str] = None


class FaultManager:
    def __init__(self) -> None:
        self._rules = []
        self._state: Dict[int, Dict[str, Optional[float]]] = {}

    def add_fault_rule(self, rule: FaultRule) -> None:
        for forbidden in (
            "trigger_time",
            "duration",
            "timestamp",
            "datetime",
            "monotonic",
            "time",
            "wall_clock",
        ):
            if hasattr(rule, forbidden):
                raise RuntimeError(
                    f"Wall-clock field '{forbidden}' is forbidden; use replay-driven fields"
                )
        raise RuntimeError("Wall-clock fault injection is forbidden; use replay-driven faults")

    def tick(self, current_time: Optional[float] = None) -> None:
        return

    def check_fault(self, station_id: str, message_type: Optional[str] = None) -> Optional[FaultRule]:
        return None


fault_manager = FaultManager()