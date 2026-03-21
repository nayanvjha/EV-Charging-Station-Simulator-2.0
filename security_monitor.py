from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Any, Deque, Dict, List, Optional, Tuple

from knowledge_graph import SecurityKnowledgeGraph
from security_types import SecurityEventType, coerce_severity

from db import (
    clear_security_events,
    get_security_event_stats,
    get_security_events_by_station,
    get_security_events_recent,
    init_db,
    insert_security_event,
)

logger = logging.getLogger("security")

# Global Knowledge Graph instance for monitor-side integration.
security_knowledge_graph = SecurityKnowledgeGraph()


class EventType(str, Enum):
    OCPP_SCHEMA_VIOLATION = SecurityEventType.OCPP_SCHEMA_VIOLATION.value
    INVALID_STATE_TRANSITION = SecurityEventType.INVALID_STATE_TRANSITION.value
    UNAUTHORIZED_ACTION = SecurityEventType.UNAUTHORIZED_ACTION.value
    RATE_LIMIT_EXCEEDED = SecurityEventType.RATE_LIMIT_EXCEEDED.value
    MALFORMED_PAYLOAD = SecurityEventType.MALFORMED_PAYLOAD.value
    UNKNOWN_MESSAGE_TYPE = SecurityEventType.UNKNOWN_MESSAGE_TYPE.value

    # Backward-compatible aliases used by older modules.
    AUTH_FAILURE = SecurityEventType.UNAUTHORIZED_ACTION.value
    DUPLICATE_TRANSACTION = SecurityEventType.INVALID_STATE_TRANSITION.value
    MALFORMED_MESSAGE = SecurityEventType.MALFORMED_PAYLOAD.value
    HEARTBEAT_FLOOD = SecurityEventType.RATE_LIMIT_EXCEEDED.value
    UNAUTHORIZED_COMMAND = SecurityEventType.UNAUTHORIZED_ACTION.value
    CHARGE_MANIPULATION_ATTACK = SecurityEventType.CHARGE_MANIPULATION_ATTACK.value
    COORDINATED_ATTACK_DETECTED = SecurityEventType.COORDINATED_ATTACK_DETECTED.value
    PROFILE_TAMPERING = SecurityEventType.PROFILE_TAMPERING.value


@dataclass
class SecurityEvent:
    timestamp: datetime
    station_id: str
    event_type: EventType
    severity: int
    description: str


class SecurityMonitor:
    def __init__(self, max_events: int = 1000, use_persistence: bool = False) -> None:
        self._events: Deque[SecurityEvent] = deque(maxlen=max_events)
        self._events_lock = Lock()
        self._kg = security_knowledge_graph
        self._kg_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="security-kg")
        self._active_transactions: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._coordinated_alert_cache: Dict[str, float] = {}
        self._coordinated_alert_cache_ttl_seconds = 30
        self._use_persistence = use_persistence
        if self._use_persistence:
            init_db()
            logger.info("SecurityMonitor persistence enabled (SQLite)")
        else:
            logger.info("SecurityMonitor running in in-memory mode")

    def _append_event(self, event: SecurityEvent) -> None:
        with self._events_lock:
            self._events.append(event)

    def _extract_context_from_description(self, description: str) -> Dict[str, Optional[str]]:
        context: Dict[str, Optional[str]] = {"id_tag": None, "transaction_id": None}
        tokens = str(description).replace(",", " ").split()
        for idx, token in enumerate(tokens):
            lower = token.lower()
            if lower.startswith("id_tag=") or lower.startswith("idtag="):
                context["id_tag"] = token.split("=", 1)[1]
                continue
            if lower.startswith("tx=") or lower.startswith("transaction_id="):
                context["transaction_id"] = token.split("=", 1)[1]
                continue
            if lower in {"id_tag", "idtag"} and idx + 1 < len(tokens):
                context["id_tag"] = tokens[idx + 1]
            if lower in {"tx", "transaction_id"} and idx + 1 < len(tokens):
                context["transaction_id"] = tokens[idx + 1]
        return context

    def _submit_kg_update(self, fn_name: str, **kwargs: Any) -> None:
        def _job() -> None:
            if fn_name == "add_security_event":
                self._kg.add_security_event(**kwargs)
            elif fn_name == "add_transaction":
                self._kg.add_transaction(**kwargs)
            elif fn_name == "link_user_chargepoint":
                self._kg.link_user_chargepoint(**kwargs)
            else:
                logger.warning("Unknown KG function requested: %s", fn_name)
                return
            self._handle_kg_alerts()

        future = self._kg_executor.submit(_job)

        def _done_callback(fut) -> None:
            try:
                fut.result()
            except Exception:
                logger.exception("Asynchronous Knowledge Graph update failed")

        future.add_done_callback(_done_callback)

    def _handle_kg_alerts(self) -> None:
        alerts = self._kg.detect_coordinated_threats()
        if not alerts:
            return

        now = time.time()
        for alert in alerts:
            affected_charge_points = list(alert.get("charge_points", []))
            involved_idtags: List[str] = []
            id_tag = alert.get("id_tag")
            if isinstance(id_tag, str) and id_tag:
                involved_idtags.append(id_tag)
            window_seconds = int(alert.get("window_seconds", 0))
            graph_explanation_path = (
                f"inmemory://security_knowledge_graph/"
                f"{alert.get('threat_type', 'UNKNOWN')}/{window_seconds}s"
            )

            dedupe_key = json.dumps(
                {
                    "threat_type": alert.get("threat_type"),
                    "event_type": alert.get("event_type"),
                    "affected_charge_points": sorted(affected_charge_points),
                    "involved_idtags": sorted(involved_idtags),
                    "time_window": window_seconds,
                },
                sort_keys=True,
            )
            last_sent = self._coordinated_alert_cache.get(dedupe_key, 0.0)
            if now - last_sent < self._coordinated_alert_cache_ttl_seconds:
                continue
            self._coordinated_alert_cache[dedupe_key] = now

            metadata = {
                "affected_charge_points": affected_charge_points,
                "involved_idtags": involved_idtags,
                "time_window": window_seconds,
                "graph_explanation_path": graph_explanation_path,
            }
            description = str(alert.get("description", "Coordinated threat detected"))
            enriched_description = f"{description} | metadata={json.dumps(metadata, sort_keys=True)}"
            coordinated_event = SecurityEvent(
                timestamp=datetime.now(timezone.utc),
                station_id="GLOBAL_NETWORK",
                event_type=EventType.COORDINATED_ATTACK_DETECTED,
                severity=coerce_severity(alert.get("severity", 9)),
                description=enriched_description,
            )
            self._append_event(coordinated_event)
            try:
                insert_security_event(
                    {
                        **event_to_record(coordinated_event),
                        "raw_message": metadata,
                    }
                )
            except Exception:
                logger.exception("Failed to persist coordinated threat event")

            logger.critical(
                "KNOWLEDGE GRAPH ALERT type=%s affected_charge_points=%s involved_idtags=%s window=%ss path=%s",
                alert.get("threat_type"),
                affected_charge_points,
                involved_idtags,
                window_seconds,
                graph_explanation_path,
            )

    def log_event(
        self,
        event_type: EventType,
        station_id: str,
        description: str,
        severity: int | str = 5,
    ) -> SecurityEvent:
        event = SecurityEvent(
            timestamp=datetime.now(timezone.utc),
            station_id=station_id,
            event_type=event_type,
            severity=coerce_severity(severity),
            description=description,
        )
        self._append_event(event)
        try:
            insert_security_event(event_to_record(event))
        except Exception:
            pass

        context = self._extract_context_from_description(description)
        self._submit_kg_update(
            "add_security_event",
            charge_point_id=station_id,
            event_type=event_type.value,
            severity=coerce_severity(severity),
            description=description,
            transaction_id=context.get("transaction_id"),
            id_tag=context.get("id_tag"),
        )

        if event_type in {EventType.AUTH_FAILURE, EventType.UNAUTHORIZED_ACTION} and context.get("id_tag"):
            self._submit_kg_update(
                "link_user_chargepoint",
                id_tag=str(context["id_tag"]),
                charge_point_id=station_id,
                relation="INITIATED_BY",
            )

        logger.warning(
            "SECURITY ALERT [%s] station=%s severity=%s - %s",
            event.event_type,
            event.station_id,
            event.severity,
            event.description,
        )
        return event

    def record_auth_request(self, station_id: str, id_tag: str, auth_success: bool) -> None:
        relation = "INITIATED_BY" if auth_success else "TARGETS"
        self._submit_kg_update(
            "link_user_chargepoint",
            id_tag=str(id_tag),
            charge_point_id=station_id,
            relation=relation,
        )

    def record_transaction_start(
        self,
        station_id: str,
        transaction_id: str,
        *,
        id_tag: Optional[str] = None,
        meter_start: Optional[float] = None,
        timestamp: Optional[datetime] = None,
    ) -> None:
        ts = (timestamp or datetime.now(timezone.utc)).timestamp()
        tx_key = (station_id, str(transaction_id))
        self._active_transactions[tx_key] = {
            "started_at": ts,
            "meter_start": meter_start,
            "id_tag": id_tag,
        }
        self._submit_kg_update(
            "add_transaction",
            charge_point_id=station_id,
            transaction_id=str(transaction_id),
            id_tag=id_tag,
            timestamp=ts,
        )

    def record_transaction_stop(
        self,
        station_id: str,
        transaction_id: str,
        *,
        id_tag: Optional[str] = None,
        meter_stop: Optional[float] = None,
        timestamp: Optional[datetime] = None,
    ) -> None:
        ts = (timestamp or datetime.now(timezone.utc)).timestamp()
        tx_key = (station_id, str(transaction_id))
        tx_state = self._active_transactions.pop(tx_key, {})
        meter_start = tx_state.get("meter_start")
        started_at = tx_state.get("started_at")
        resolved_id_tag = id_tag or tx_state.get("id_tag")

        energy_delivered: Optional[float] = None
        charging_duration: Optional[float] = None
        average_power: Optional[float] = None

        if isinstance(meter_start, (int, float)) and isinstance(meter_stop, (int, float)) and meter_stop >= meter_start:
            energy_delivered = float(meter_stop - meter_start)
        if isinstance(started_at, (int, float)):
            charging_duration = max(0.0, ts - float(started_at))
        if energy_delivered is not None and charging_duration and charging_duration > 0:
            average_power = (energy_delivered * 3600.0) / charging_duration

        self._submit_kg_update(
            "add_transaction",
            charge_point_id=station_id,
            transaction_id=str(transaction_id),
            id_tag=resolved_id_tag,
            energy_delivered=energy_delivered,
            charging_duration=charging_duration,
            average_power=average_power,
            timestamp=ts,
        )

    def get_recent_events(self, limit: int = 100) -> List[SecurityEvent]:
        if limit <= 0:
            return []
        if self._use_persistence or not self._events:
            return [record_to_event(row) for row in get_security_events_recent(limit=limit)]
        with self._events_lock:
            return list(self._events)[-limit:]

    def get_events_for_station(self, station_id: str) -> List[SecurityEvent]:
        if self._use_persistence or not self._events:
            return [record_to_event(row) for row in get_security_events_by_station(station_id)]
        with self._events_lock:
            return [event for event in self._events if event.station_id == station_id]

    def get_stats_by_type_or_severity(self) -> Dict[str, Dict[str, int]]:
        if self._use_persistence:
            return get_security_event_stats()
        stats: Dict[str, Dict[str, int]] = {"by_type": {}, "by_severity": {}}
        with self._events_lock:
            events = list(self._events)
        for event in events:
            stats["by_type"][event.event_type.value] = stats["by_type"].get(event.event_type.value, 0) + 1
            stats["by_severity"][event.severity] = stats["by_severity"].get(event.severity, 0) + 1
        return stats

    def clear_events(self) -> None:
        with self._events_lock:
            self._events.clear()
        if self._use_persistence:
            clear_security_events()


def event_to_record(event: SecurityEvent) -> Dict[str, object]:
    return {
        "timestamp": event.timestamp,
        "station_id": event.station_id,
        "charge_point_id": event.station_id,
        "event_type": event.event_type.value,
        "severity": event.severity,
        "description": event.description,
    }


def record_to_event(record: Dict[str, object]) -> SecurityEvent:
    event_type_value = str(record["event_type"])
    try:
        event_type = EventType(event_type_value)
    except ValueError:
        event_type = EventType.UNKNOWN_MESSAGE_TYPE
    return SecurityEvent(
        timestamp=record["timestamp"],
        station_id=str(record.get("station_id") or record.get("charge_point_id") or "UNKNOWN"),
        event_type=event_type,
        severity=int(record["severity"]),
        description=record["description"],
    )


def event_to_dict(event: SecurityEvent) -> Dict[str, str]:
    return {
        "timestamp": event.timestamp.isoformat(),
        "station_id": event.station_id,
        "charge_point_id": event.station_id,
        "event_type": event.event_type.value,
        "severity": str(event.severity),
        "description": event.description,
    }


USE_SECURITY_PERSISTENCE = os.getenv("SECURITY_PERSISTENCE", "false").lower() in {
    "1",
    "true",
    "yes",
}

security_monitor = SecurityMonitor(use_persistence=USE_SECURITY_PERSISTENCE)
