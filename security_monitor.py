from __future__ import annotations

import logging
import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Deque, Dict, List

from db import (
    clear_security_events,
    get_security_event_stats,
    get_security_events_by_station,
    get_security_events_recent,
    init_db,
    insert_security_event,
)

logger = logging.getLogger("security")


class EventType(str, Enum):
    AUTH_FAILURE = "AUTH_FAILURE"
    DUPLICATE_TRANSACTION = "DUPLICATE_TRANSACTION"
    MALFORMED_MESSAGE = "MALFORMED_MESSAGE"
    HEARTBEAT_FLOOD = "HEARTBEAT_FLOOD"
    UNAUTHORIZED_COMMAND = "UNAUTHORIZED_COMMAND"


@dataclass
class SecurityEvent:
    timestamp: datetime
    station_id: str
    event_type: EventType
    severity: str
    description: str


class SecurityMonitor:
    def __init__(self, max_events: int = 1000, use_persistence: bool = False) -> None:
        self._events: Deque[SecurityEvent] = deque(maxlen=max_events)
        self._use_persistence = use_persistence
        if self._use_persistence:
            init_db()
            logger.info("SecurityMonitor persistence enabled (SQLite)")
        else:
            logger.info("SecurityMonitor running in in-memory mode")

    def log_event(
        self,
        event_type: EventType,
        station_id: str,
        description: str,
        severity: str = "low",
    ) -> SecurityEvent:
        event = SecurityEvent(
            timestamp=datetime.now(timezone.utc),
            station_id=station_id,
            event_type=event_type,
            severity=severity,
            description=description,
        )
        self._events.append(event)
        try:
            insert_security_event(event_to_record(event))
        except Exception:
            pass
        logger.warning(
            "SECURITY ALERT [%s] station=%s severity=%s - %s",
            event.event_type,
            event.station_id,
            event.severity,
            event.description,
        )
        return event

    def get_recent_events(self, limit: int = 100) -> List[SecurityEvent]:
        if limit <= 0:
            return []
        if self._use_persistence or not self._events:
            return [record_to_event(row) for row in get_security_events_recent(limit=limit)]
        return list(self._events)[-limit:]

    def get_events_for_station(self, station_id: str) -> List[SecurityEvent]:
        if self._use_persistence or not self._events:
            return [record_to_event(row) for row in get_security_events_by_station(station_id)]
        return [event for event in self._events if event.station_id == station_id]

    def get_stats_by_type_or_severity(self) -> Dict[str, Dict[str, int]]:
        if self._use_persistence:
            return get_security_event_stats()
        stats: Dict[str, Dict[str, int]] = {"by_type": {}, "by_severity": {}}
        for event in self._events:
            stats["by_type"][event.event_type.value] = stats["by_type"].get(event.event_type.value, 0) + 1
            stats["by_severity"][event.severity] = stats["by_severity"].get(event.severity, 0) + 1
        return stats

    def clear_events(self) -> None:
        self._events.clear()
        if self._use_persistence:
            clear_security_events()


def event_to_record(event: SecurityEvent) -> Dict[str, object]:
    return {
        "timestamp": event.timestamp,
        "station_id": event.station_id,
        "event_type": event.event_type.value,
        "severity": event.severity,
        "description": event.description,
    }


def record_to_event(record: Dict[str, object]) -> SecurityEvent:
    return SecurityEvent(
        timestamp=record["timestamp"],
        station_id=record["station_id"],
        event_type=EventType(record["event_type"]),
        severity=record["severity"],
        description=record["description"],
    )


def event_to_dict(event: SecurityEvent) -> Dict[str, str]:
    return {
        "timestamp": event.timestamp.isoformat(),
        "station_id": event.station_id,
        "event_type": event.event_type.value,
        "severity": event.severity,
        "description": event.description,
    }


USE_SECURITY_PERSISTENCE = os.getenv("SECURITY_PERSISTENCE", "false").lower() in {
    "1",
    "true",
    "yes",
}

security_monitor = SecurityMonitor(use_persistence=USE_SECURITY_PERSISTENCE)