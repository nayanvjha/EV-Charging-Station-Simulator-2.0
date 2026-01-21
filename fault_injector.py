import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, Optional, Union

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
    trigger_time: Union[float, datetime]
    duration: Optional[float] = None
    message_type: Optional[str] = None


class FaultManager:
    def __init__(self) -> None:
        self._rules = []
        self._state: Dict[int, Dict[str, Optional[float]]] = {}
        self._start_time = time.monotonic()

    def add_fault_rule(self, rule: FaultRule) -> None:
        self._rules.append(rule)
        self._state[id(rule)] = {
            "active": False,
            "end_elapsed": None,
            "end_dt": None,
        }
        logger.info(
            "Registered fault: %s station=%s trigger=%s duration=%s message_type=%s",
            rule.fault_type,
            rule.station_id,
            rule.trigger_time,
            rule.duration,
            rule.message_type,
        )

    def tick(self, current_time: Optional[float] = None) -> None:
        now_mono = current_time if current_time is not None else time.monotonic()
        elapsed = now_mono - self._start_time
        now_dt = datetime.now(timezone.utc)

        for rule in self._rules:
            state = self._state.get(id(rule))
            if not state:
                continue

            if isinstance(rule.trigger_time, datetime):
                if not state["active"] and now_dt >= rule.trigger_time:
                    state["active"] = True
                    if rule.duration:
                        state["end_dt"] = rule.trigger_time + timedelta(seconds=rule.duration)
                elif state["active"] and state["end_dt"] and now_dt >= state["end_dt"]:
                    state["active"] = False
            else:
                trigger = float(rule.trigger_time)
                if not state["active"] and elapsed >= trigger:
                    state["active"] = True
                    if rule.duration:
                        state["end_elapsed"] = trigger + rule.duration
                elif state["active"] and state["end_elapsed"] and elapsed >= state["end_elapsed"]:
                    state["active"] = False

    def check_fault(self, station_id: str, message_type: Optional[str] = None) -> Optional[FaultRule]:
        for rule in self._rules:
            state = self._state.get(id(rule))
            if not state or not state.get("active"):
                continue
            if rule.station_id not in (station_id, "ALL"):
                continue
            if rule.message_type and message_type and rule.message_type != message_type:
                continue
            if rule.message_type and message_type is None:
                continue
            return rule
        return None


fault_manager = FaultManager()