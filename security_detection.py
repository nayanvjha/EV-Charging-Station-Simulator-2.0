from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from security_monitor import EventType, security_monitor

logger = logging.getLogger("security_detection")


@dataclass
class DetectionRule:
    name: str
    event_type: str
    threshold: int
    window_seconds: int
    station_scope: bool
    severity: str
    description: str
    alert_event_type: Optional[str] = None


class FlowTracker:
    def __init__(self) -> None:
        self._events: Dict[Tuple[Optional[str], str], List[float]] = {}

    def record_event(self, event_type: str, station_id: Optional[str] = None) -> None:
        key = (station_id, event_type)
        now = time.monotonic()
        timestamps = self._events.setdefault(key, [])
        timestamps.append(now)

    def _prune(self, now: float, window_seconds: int) -> None:
        cutoff = now - window_seconds
        for key, timestamps in list(self._events.items()):
            while timestamps and timestamps[0] < cutoff:
                timestamps.pop(0)
            if not timestamps:
                self._events.pop(key, None)

    def get_count(self, event_type: str, window_seconds: int, station_id: Optional[str] = None) -> int:
        now = time.monotonic()
        self._prune(now, window_seconds)
        key = (station_id, event_type)
        return len(self._events.get(key, []))

    def get_counts_snapshot(self, window_seconds: int) -> Dict[str, object]:
        now = time.monotonic()
        self._prune(now, window_seconds)
        global_counts: Dict[str, int] = {}
        by_station: Dict[str, Dict[str, int]] = {}
        for (station_id, event_type), timestamps in self._events.items():
            count = len(timestamps)
            if station_id is None:
                global_counts[event_type] = global_counts.get(event_type, 0) + count
            else:
                station_map = by_station.setdefault(station_id, {})
                station_map[event_type] = station_map.get(event_type, 0) + count
        return {"global": global_counts, "by_station": by_station}


class RuleEvaluator:
    def __init__(
        self,
        tracker: FlowTracker,
        rules_path: Optional[str] = None,
        interval_seconds: int = 5,
    ) -> None:
        self.tracker = tracker
        self.rules_path = rules_path or os.getenv("SECURITY_RULES_PATH", "detection_rules.json")
        self.interval_seconds = interval_seconds
        self._rules: List[DetectionRule] = []
        self._last_triggered: Dict[Tuple[str, Optional[str]], float] = {}
        self._enabled = os.getenv("SECURITY_RULES_ENABLED", "true").lower() in {"1", "true", "yes"}
        self._running = False
        self.reload_rules()

    def reload_rules(self) -> None:
        try:
            with open(self.rules_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self._rules = [DetectionRule(**rule) for rule in raw]
            logger.info("Loaded %d detection rules from %s", len(self._rules), self.rules_path)
        except Exception as exc:
            logger.warning("Failed to load detection rules: %s", exc)
            self._rules = []

    def _map_alert_event_type(self, rule: DetectionRule) -> EventType:
        if rule.alert_event_type:
            return EventType(rule.alert_event_type)
        if rule.event_type in {"MALFORMED_PAYLOAD"}:
            return EventType.MALFORMED_MESSAGE
        if rule.event_type in {"UNKNOWN_STATION"}:
            return EventType.UNAUTHORIZED_COMMAND
        if rule.event_type in {"AUTH_REQUEST"}:
            return EventType.AUTH_FAILURE
        return EventType.HEARTBEAT_FLOOD

    async def run(self) -> None:
        if not self._enabled:
            logger.info("Security detection rules disabled")
            return
        if self._running:
            return
        self._running = True
        while True:
            self.evaluate_rules()
            await asyncio.sleep(self.interval_seconds)

    def evaluate_rules(self) -> None:
        if not self._rules:
            return
        now = time.monotonic()
        for rule in self._rules:
            if rule.station_scope:
                self._evaluate_station_rule(rule, now)
            else:
                self._evaluate_global_rule(rule, now)

    def _evaluate_station_rule(self, rule: DetectionRule, now: float) -> None:
        station_ids = {station for (station, event_type) in self.tracker._events.keys() if event_type == rule.event_type}
        for station_id in station_ids:
            count = self.tracker.get_count(rule.event_type, rule.window_seconds, station_id)
            if count > rule.threshold:
                self._emit_alert(rule, station_id, count, now)

    def _evaluate_global_rule(self, rule: DetectionRule, now: float) -> None:
        count = self.tracker.get_count(rule.event_type, rule.window_seconds, None)
        if count > rule.threshold:
            self._emit_alert(rule, None, count, now)

    def _emit_alert(self, rule: DetectionRule, station_id: Optional[str], count: int, now: float) -> None:
        key = (rule.name, station_id)
        last = self._last_triggered.get(key)
        if last and now - last < rule.window_seconds:
            return
        self._last_triggered[key] = now
        alert_type = self._map_alert_event_type(rule)
        station_label = station_id or "GLOBAL"
        description = f"{rule.name}: {rule.description} ({count}/{rule.threshold} in {rule.window_seconds}s)"
        security_monitor.log_event(
            alert_type,
            station_label,
            description,
            severity=rule.severity,
        )
        logger.warning("Rule match: %s station=%s count=%s", rule.name, station_label, count)


flow_tracker = FlowTracker()
rule_evaluator = RuleEvaluator(flow_tracker)
