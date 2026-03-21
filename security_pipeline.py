from __future__ import annotations

import json
import logging
import math
import os
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
from typing import Any, Deque, Dict, List, Optional, Tuple

from security_types import SecurityEventType
from db import insert_security_event

logger = logging.getLogger("security_pipeline")

_MAX_SESSION_DURATION_SECONDS = 24 * 60 * 60
_MAX_SCHEDULE_PERIODS = 96
_DEFAULT_CMA_MAX_CURRENT_AC_AMP = 32.0
_DEFAULT_CMA_MAX_CURRENT_DC_AMP = 200.0
_DEFAULT_CMA_MIN_CURRENT_AMP = 6.0
_DEFAULT_PROFILE_UPDATES_PER_MINUTE = 6
_DEFAULT_CONFIG_CHANGES_PER_5MIN = 8
_DEFAULT_PROFILE_STACK_LEVEL_LIMIT = 5

_POWER_DELIVERY_CONFIG_KEYS = {
    "MAXCHARGINGCURRENT",
    "CHARGECURRENTLIMIT",
    "CHARGEPROFILEMAXSTACKLEVEL",
    "MAXCURRENT",
    "POWERLIMIT",
}

_SEVERITY_TEXT_TO_NUMERIC = {
    "LOW": 3,
    "MEDIUM": 5,
    "HIGH": 8,
    "CRITICAL": 10,
}
_EVENT_DEFAULT_SEVERITY = {
    SecurityEventType.CHARGE_MANIPULATION_ATTACK.value: "CRITICAL",
    SecurityEventType.PROFILE_TAMPERING.value: "HIGH",
    SecurityEventType.DISTRIBUTED_DOS_ATTACK.value: "CRITICAL",
    SecurityEventType.ISO15118_SESSION_ABUSE.value: "HIGH",
}


def _load_detection_severity_map() -> Dict[str, int]:
    rules_path = os.getenv("SECURITY_RULES_PATH", "detection_rules.json")
    defaults = {
        SecurityEventType.CHARGE_MANIPULATION_ATTACK.value: 10,
        SecurityEventType.PROFILE_TAMPERING.value: 8,
        SecurityEventType.DISTRIBUTED_DOS_ATTACK.value: 10,
        SecurityEventType.ISO15118_SESSION_ABUSE.value: 8,
    }
    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return defaults

    if not isinstance(data, dict):
        return defaults

    rules = data.get("legacy_threshold_rules")
    if not isinstance(rules, list):
        return defaults

    severity_by_event = dict(defaults)
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        event_type = rule.get("alert_event_type")
        raw_severity = str(rule.get("severity", "")).strip().upper()
        if not event_type or raw_severity not in _SEVERITY_TEXT_TO_NUMERIC:
            continue
        severity_by_event[str(event_type)] = _SEVERITY_TEXT_TO_NUMERIC[raw_severity]
    return severity_by_event


_DETECTION_SEVERITY_BY_EVENT = _load_detection_severity_map()
_VALIDATOR_LOCK = RLock()
_COMMAND_SLIDING_WINDOWS: Dict[Tuple[str, str], Deque[float]] = {}


class OcppAction(str, Enum):
    BOOT_NOTIFICATION = "BootNotification"
    AUTHORIZE = "Authorize"
    START_TRANSACTION = "StartTransaction"
    METER_VALUES = "MeterValues"
    STOP_TRANSACTION = "StopTransaction"
    SET_CHARGING_PROFILE = "SetChargingProfile"
    CHANGE_CONFIGURATION = "ChangeConfiguration"


class OcppState(str, Enum):
    INIT = "INIT"
    BOOTED = "BOOTED"
    AUTHORIZED = "AUTHORIZED"
    TRANSACTION_ACTIVE = "TRANSACTION_ACTIVE"


_ALLOWED_ACTIONS = {
    OcppAction.BOOT_NOTIFICATION.value,
    OcppAction.AUTHORIZE.value,
    OcppAction.START_TRANSACTION.value,
    OcppAction.METER_VALUES.value,
    OcppAction.STOP_TRANSACTION.value,
    OcppAction.SET_CHARGING_PROFILE.value,
    OcppAction.CHANGE_CONFIGURATION.value,
}


@dataclass
class RateState:
    message_timestamps: List[float] = field(default_factory=list)
    failed_auth_timestamps: List[float] = field(default_factory=list)


class OcppStateMachine:
    def __init__(self) -> None:
        self._states: Dict[str, OcppState] = {}
        self._lock = RLock()

    def get_state(self, charge_point_id: str) -> OcppState:
        with self._lock:
            return self._states.get(charge_point_id, OcppState.INIT)

    def _set_state(self, charge_point_id: str, state: OcppState) -> None:
        with self._lock:
            self._states[charge_point_id] = state

    def reset_after_stop(self, charge_point_id: str) -> None:
        self._set_state(charge_point_id, OcppState.BOOTED)

    def check_allowed_transition(self, charge_point_id: str, new_action: str) -> bool:
        try:
            prev_state = self.get_state(charge_point_id)
            if new_action not in _ALLOWED_ACTIONS:
                self._log_invalid(charge_point_id, prev_state, new_action)
                return False

            if prev_state == OcppState.INIT:
                if new_action == OcppAction.BOOT_NOTIFICATION.value:
                    self._set_state(charge_point_id, OcppState.BOOTED)
                    return True
                self._log_invalid(charge_point_id, prev_state, new_action)
                return False

            if prev_state == OcppState.BOOTED:
                if new_action == OcppAction.BOOT_NOTIFICATION.value:
                    return True
                if new_action == OcppAction.AUTHORIZE.value:
                    self._set_state(charge_point_id, OcppState.AUTHORIZED)
                    return True
                self._log_invalid(charge_point_id, prev_state, new_action)
                return False

            if prev_state == OcppState.AUTHORIZED:
                if new_action == OcppAction.AUTHORIZE.value:
                    return True
                if new_action == OcppAction.START_TRANSACTION.value:
                    self._set_state(charge_point_id, OcppState.TRANSACTION_ACTIVE)
                    return True
                if new_action == OcppAction.BOOT_NOTIFICATION.value:
                    self._set_state(charge_point_id, OcppState.BOOTED)
                    return True
                self._log_invalid(charge_point_id, prev_state, new_action)
                return False

            if prev_state == OcppState.TRANSACTION_ACTIVE:
                if new_action == OcppAction.METER_VALUES.value:
                    return True
                if new_action == OcppAction.STOP_TRANSACTION.value:
                    self.reset_after_stop(charge_point_id)
                    return True
                if new_action in {OcppAction.SET_CHARGING_PROFILE.value, OcppAction.CHANGE_CONFIGURATION.value}:
                    return True
                self._log_invalid(charge_point_id, prev_state, new_action)
                return False

            self._log_invalid(charge_point_id, prev_state, new_action)
            return False
        except Exception:
            logger.exception("State transition check failed")
            return False

    def _log_invalid(self, charge_point_id: str, prev_state: OcppState, attempted_action: str) -> None:
        insert_security_event(
            {
                "charge_point_id": charge_point_id,
                "event_type": SecurityEventType.INVALID_STATE_TRANSITION.value,
                "severity": 7,
                "description": (
                    f"Invalid transition for charge_point_id={charge_point_id}: "
                    f"state={prev_state.value}, action={attempted_action}"
                ),
            }
        )


class OcppRateLimiter:
    def __init__(self, window_seconds: int = 60) -> None:
        self.window_seconds = window_seconds
        self._states: Dict[str, RateState] = {}
        self._lock = RLock()

    def _state(self, charge_point_id: str) -> RateState:
        state = self._states.get(charge_point_id)
        if state is None:
            state = RateState()
            self._states[charge_point_id] = state
        return state

    def _prune(self, timestamps: List[float], now: float) -> None:
        cutoff = now - self.window_seconds
        valid = [ts for ts in timestamps if ts >= cutoff]
        timestamps.clear()
        timestamps.extend(valid)

    def check(
        self,
        charge_point_id: str,
        action: str,
        was_auth_successful: Optional[bool] = None,
        raw_message: Any = None,
    ) -> bool:
        try:
            if not isinstance(charge_point_id, str) or not charge_point_id.strip():
                return False

            now = time.monotonic()
            with self._lock:
                state = self._state(charge_point_id)
                self._prune(state.message_timestamps, now)
                self._prune(state.failed_auth_timestamps, now)

                state.message_timestamps.append(now)
                if action == OcppAction.AUTHORIZE.value and was_auth_successful is False:
                    state.failed_auth_timestamps.append(now)

                message_count = len(state.message_timestamps)
                failed_auth_count = len(state.failed_auth_timestamps)

                if failed_auth_count > 10:
                    insert_security_event(
                        {
                            "charge_point_id": charge_point_id,
                            "event_type": SecurityEventType.RATE_LIMIT_EXCEEDED.value,
                            "severity": 6,
                            "description": (
                                f"Failed Authorize threshold exceeded: {failed_auth_count} in {self.window_seconds}s"
                            ),
                            "raw_message": raw_message,
                        }
                    )
                    return False

                if message_count > 60:
                    insert_security_event(
                        {
                            "charge_point_id": charge_point_id,
                            "event_type": SecurityEventType.RATE_LIMIT_EXCEEDED.value,
                            "severity": 4,
                            "description": f"Message threshold exceeded: {message_count} in {self.window_seconds}s",
                            "raw_message": raw_message,
                        }
                    )
                    return False

                if not state.message_timestamps and not state.failed_auth_timestamps:
                    self._states.pop(charge_point_id, None)

                return True
        except Exception:
            logger.exception("Rate limiter check failed")
            return False


@dataclass
class _RollingMetricState:
    values: Deque[float] = field(default_factory=deque)
    total: float = 0.0
    total_sq: float = 0.0


class BehavioralAnomalyDetector:
    """Thread-safe rolling behavioral anomaly detector using O(1) updates."""

    _METRICS = ("energy_delivered", "charging_duration", "average_power")

    def __init__(
        self,
        window_size: int = 60,
        min_samples: int = 10,
        sigma_threshold: float = 3.0,
    ) -> None:
        self.window_size = max(5, window_size)
        self.min_samples = max(3, min_samples)
        self.sigma_threshold = max(1.0, sigma_threshold)
        self._states: Dict[str, Dict[str, _RollingMetricState]] = {}
        self._lock = RLock()

    def _state(self, charge_point_id: str, metric: str) -> _RollingMetricState:
        cp_state = self._states.get(charge_point_id)
        if cp_state is None:
            cp_state = {}
            self._states[charge_point_id] = cp_state
        metric_state = cp_state.get(metric)
        if metric_state is None:
            metric_state = _RollingMetricState()
            cp_state[metric] = metric_state
        return metric_state

    def _append(self, state: _RollingMetricState, value: float) -> None:
        if len(state.values) >= self.window_size:
            old = state.values.popleft()
            state.total -= old
            state.total_sq -= old * old
        state.values.append(value)
        state.total += value
        state.total_sq += value * value

    @staticmethod
    def _mean_std(state: _RollingMetricState) -> Tuple[float, float]:
        n = len(state.values)
        if n == 0:
            return 0.0, 0.0
        mean = state.total / n
        variance = (state.total_sq / n) - (mean * mean)
        if variance <= 0:
            return mean, 0.0
        return mean, math.sqrt(variance)

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return float(text)
            except ValueError:
                return None
        return None

    def _emit_anomaly(
        self,
        *,
        charge_point_id: str,
        command: str,
        metric: str,
        value: float,
        mean: float,
        std_dev: float,
        raw_message: Any,
    ) -> None:
        severity = _DETECTION_SEVERITY_BY_EVENT.get(
            SecurityEventType.CHARGE_MANIPULATION_ATTACK.value,
            _SEVERITY_TEXT_TO_NUMERIC["CRITICAL"],
        )
        attempted_value = {
            "metric": metric,
            "value": value,
            "mean": mean,
            "std_dev": std_dev,
            "sigma_threshold": self.sigma_threshold,
        }
        metadata = {
            "charge_point_id": charge_point_id,
            "command": command,
            "attempted_value": attempted_value,
        }
        logger.warning(
            json.dumps(
                {
                    "event": "SECURITY_BEHAVIORAL_ANOMALY",
                    "event_type": SecurityEventType.CHARGE_MANIPULATION_ATTACK.value,
                    "severity": severity,
                    "description": f"Behavioral deviation > {self.sigma_threshold} sigma detected",
                    "metadata": metadata,
                },
                ensure_ascii=False,
            )
        )
        insert_security_event(
            {
                "charge_point_id": charge_point_id,
                "event_type": SecurityEventType.CHARGE_MANIPULATION_ATTACK.value,
                "severity": severity,
                "description": (
                    f"Behavioral anomaly for {metric}: value={value:.4f}, "
                    f"mean={mean:.4f}, std={std_dev:.4f}"
                ),
                "raw_message": {"frame": raw_message, "metadata": metadata},
            }
        )

    def evaluate(
        self,
        *,
        charge_point_id: str,
        command: str,
        metrics: Dict[str, Any],
        raw_message: Any,
    ) -> bool:
        anomalies: List[Tuple[str, float, float, float]] = []
        with self._lock:
            for metric in self._METRICS:
                value = self._to_float(metrics.get(metric))
                if value is None:
                    continue
                state = self._state(charge_point_id, metric)
                if len(state.values) >= self.min_samples:
                    mean, std_dev = self._mean_std(state)
                    if std_dev > 0 and abs(value - mean) > (self.sigma_threshold * std_dev):
                        anomalies.append((metric, value, mean, std_dev))
                self._append(state, value)

        for metric, value, mean, std_dev in anomalies:
            self._emit_anomaly(
                charge_point_id=charge_point_id,
                command=command,
                metric=metric,
                value=value,
                mean=mean,
                std_dev=std_dev,
                raw_message=raw_message,
            )
        return bool(anomalies)


def validate_ocpp_message(message: Any, connection_context: Dict[str, str]) -> bool:
    def _event_severity(event_type: SecurityEventType) -> int:
        return _DETECTION_SEVERITY_BY_EVENT.get(
            event_type.value,
            _SEVERITY_TEXT_TO_NUMERIC[_EVENT_DEFAULT_SEVERITY[event_type.value]],
        )

    def _emit_violation(
        *,
        event_type: SecurityEventType,
        description: str,
        command: str,
        attempted_value: Any,
        charge_point_id: str,
        raw_message: Any,
    ) -> None:
        severity = _event_severity(event_type)
        metadata = {
            "charge_point_id": charge_point_id,
            "command": command,
            "attempted_value": attempted_value,
        }
        logger.warning(
            json.dumps(
                {
                    "event": "SECURITY_VALIDATION_VIOLATION",
                    "event_type": event_type.value,
                    "severity": severity,
                    "description": description,
                    "metadata": metadata,
                },
                ensure_ascii=False,
            )
        )
        insert_security_event(
            {
                "charge_point_id": charge_point_id,
                "event_type": event_type.value,
                "severity": severity,
                "description": description,
                "raw_message": {"frame": raw_message, "metadata": metadata},
            }
        )

    def _to_float(value: Any) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return float(text)
            except ValueError:
                return None
        return None

    def _to_int(value: Any) -> Optional[int]:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return int(text)
            except ValueError:
                return None
        return None

    def _check_sliding_limit(charge_point_id: str, command: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        key = (charge_point_id, command)
        with _VALIDATOR_LOCK:
            bucket = _COMMAND_SLIDING_WINDOWS.get(key)
            if bucket is None:
                bucket = deque()
                _COMMAND_SLIDING_WINDOWS[key] = bucket
            cutoff = now - window_seconds
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True

    def _extract_behavioral_metrics(action: str, payload: Dict[str, Any]) -> Dict[str, float]:
        metrics: Dict[str, float] = {}

        energy = _to_float(payload.get("energy_delivered"))
        duration = _to_float(payload.get("charging_duration"))
        average_power = _to_float(payload.get("average_power"))

        if action == OcppAction.STOP_TRANSACTION.value:
            meter_stop = _to_float(payload.get("meterStop"))
            meter_start = _to_float(payload.get("meterStart"))
            if energy is None and meter_stop is not None and meter_start is not None and meter_stop >= meter_start:
                energy = meter_stop - meter_start
            if duration is None:
                transaction_data = payload.get("transactionData")
                if isinstance(transaction_data, list):
                    duration = float(len(transaction_data))

        if action == OcppAction.METER_VALUES.value:
            meter_values = payload.get("meterValue")
            if isinstance(meter_values, list):
                for mv in reversed(meter_values):
                    if not isinstance(mv, dict):
                        continue
                    sampled_values = mv.get("sampledValue")
                    if not isinstance(sampled_values, list):
                        continue
                    for sample in sampled_values:
                        if not isinstance(sample, dict):
                            continue
                        measurand = str(sample.get("measurand", "")).strip().lower()
                        sample_value = _to_float(sample.get("value"))
                        if sample_value is None:
                            continue
                        if energy is None and "energy.active.import.register" in measurand:
                            energy = sample_value
                        if average_power is None and "power.active.import" in measurand:
                            average_power = sample_value
                    if energy is not None and average_power is not None:
                        break

        if average_power is None and energy is not None and duration is not None and duration > 0:
            # Assume energy_delivered in Wh and duration in seconds -> average power in W.
            average_power = (energy * 3600.0) / duration

        if energy is not None:
            metrics["energy_delivered"] = energy
        if duration is not None:
            metrics["charging_duration"] = duration
        if average_power is not None:
            metrics["average_power"] = average_power
        return metrics

    try:
        original = message
        parsed = message
        charge_point_id = connection_context.get("charge_point_id", "UNKNOWN")
        if not isinstance(charge_point_id, str) or not charge_point_id.strip():
            charge_point_id = "UNKNOWN"

        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except json.JSONDecodeError:
                _log_schema_violation(connection_context, "Failed to parse JSON message", original)
                return False

        if parsed is None or not isinstance(parsed, list):
            _log_schema_violation(connection_context, "Message must be OCPP array frame", original)
            return False

        if len(parsed) < 2:
            _log_schema_violation(
                connection_context,
                "OCPP frame must contain at least messageTypeId and uniqueId",
                original,
            )
            return False

        message_type_id = parsed[0]
        unique_id = parsed[1]

        if not isinstance(message_type_id, int) or message_type_id not in {2, 3, 4}:
            _log_schema_violation(connection_context, f"Unsupported messageTypeId={message_type_id}", original)
            return False

        if not isinstance(unique_id, str) or not unique_id:
            _log_schema_violation(connection_context, "uniqueId must be a non-empty string", original)
            return False

        if message_type_id == 2:
            if len(parsed) < 4:
                _log_schema_violation(connection_context, "CALL requires [2, uniqueId, action, payload]", original)
                return False

            action = parsed[2]
            payload = parsed[3]
            if not isinstance(action, str) or not action:
                _log_schema_violation(connection_context, "CALL action must be non-empty string", original)
                return False
            if action not in _ALLOWED_ACTIONS:
                _log_schema_violation(connection_context, f"Unsupported CALL action={action}", original)
                return False
            if not isinstance(payload, dict):
                _log_schema_violation(connection_context, "CALL payload must be object", original)
                return False

            if action in {OcppAction.METER_VALUES.value, OcppAction.STOP_TRANSACTION.value}:
                behavior_metrics = _extract_behavioral_metrics(action, payload)
                if behavior_metrics:
                    behavioral_anomaly_detector.evaluate(
                        charge_point_id=charge_point_id,
                        command=action,
                        metrics=behavior_metrics,
                        raw_message=original,
                    )

            if action == OcppAction.SET_CHARGING_PROFILE.value:
                if not _check_sliding_limit(charge_point_id, action, _DEFAULT_PROFILE_UPDATES_PER_MINUTE, 60):
                    _emit_violation(
                        event_type=SecurityEventType.PROFILE_TAMPERING,
                        description="Rapid sequential SetChargingProfile overwrites detected",
                        command=action,
                        attempted_value={"rate_limit_per_minute": _DEFAULT_PROFILE_UPDATES_PER_MINUTE},
                        charge_point_id=charge_point_id,
                        raw_message=original,
                    )
                    return False

                profile = payload.get("csChargingProfiles")
                if profile is None or not isinstance(profile, dict):
                    _emit_violation(
                        event_type=SecurityEventType.PROFILE_TAMPERING,
                        description="Missing or invalid csChargingProfiles object",
                        command=action,
                        attempted_value=profile,
                        charge_point_id=charge_point_id,
                        raw_message=original,
                    )
                    return False

                stack_level = profile.get("stackLevel")
                if stack_level is None or not isinstance(stack_level, int) or isinstance(stack_level, bool):
                    _emit_violation(
                        event_type=SecurityEventType.PROFILE_TAMPERING,
                        description="stackLevel must be a non-null integer",
                        command=action,
                        attempted_value=stack_level,
                        charge_point_id=charge_point_id,
                        raw_message=original,
                    )
                    return False
                if stack_level < 0 or stack_level > _DEFAULT_PROFILE_STACK_LEVEL_LIMIT:
                    _emit_violation(
                        event_type=SecurityEventType.PROFILE_TAMPERING,
                        description="stackLevel integrity violation",
                        command=action,
                        attempted_value=stack_level,
                        charge_point_id=charge_point_id,
                        raw_message=original,
                    )
                    return False

                purpose = profile.get("chargingProfilePurpose")
                purpose_key = str(purpose or "UNKNOWN").strip().upper() or "UNKNOWN"
                overwrite_key = f"{action}:{purpose_key}:{stack_level}"
                if not _check_sliding_limit(charge_point_id, overwrite_key, 2, 15):
                    _emit_violation(
                        event_type=SecurityEventType.PROFILE_TAMPERING,
                        description="Rapid sequential profile overwrite for same stackLevel detected",
                        command=action,
                        attempted_value={"purpose": purpose_key, "stackLevel": stack_level},
                        charge_point_id=charge_point_id,
                        raw_message=original,
                    )
                    return False

                schedule = profile.get("chargingSchedule")
                if schedule is None or not isinstance(schedule, dict):
                    _emit_violation(
                        event_type=SecurityEventType.PROFILE_TAMPERING,
                        description="chargingSchedule must be a non-null object",
                        command=action,
                        attempted_value=schedule,
                        charge_point_id=charge_point_id,
                        raw_message=original,
                    )
                    return False

                current_type = str(
                    payload.get("currentType")
                    or profile.get("currentType")
                    or schedule.get("chargingRateUnit")
                    or profile.get("chargingRateUnit")
                    or ""
                ).strip().upper()
                max_current_amp = (
                    _DEFAULT_CMA_MAX_CURRENT_DC_AMP if current_type == "DC" else _DEFAULT_CMA_MAX_CURRENT_AC_AMP
                )

                has_direct_current = "current" in payload or "current" in profile
                if has_direct_current:
                    direct_current = payload.get("current", profile.get("current"))
                    if direct_current is None:
                        _emit_violation(
                            event_type=SecurityEventType.CHARGE_MANIPULATION_ATTACK,
                            description="SetChargingProfile.current must be non-null",
                            command=action,
                            attempted_value=direct_current,
                            charge_point_id=charge_point_id,
                            raw_message=original,
                        )
                        return False
                    direct_current_amp = _to_float(direct_current)
                    if direct_current_amp is None:
                        _emit_violation(
                            event_type=SecurityEventType.CHARGE_MANIPULATION_ATTACK,
                            description="SetChargingProfile.current must be numeric",
                            command=action,
                            attempted_value=direct_current,
                            charge_point_id=charge_point_id,
                            raw_message=original,
                        )
                        return False
                    if direct_current_amp < 0:
                        _emit_violation(
                            event_type=SecurityEventType.CHARGE_MANIPULATION_ATTACK,
                            description="Negative SetChargingProfile.current rejected",
                            command=action,
                            attempted_value=direct_current_amp,
                            charge_point_id=charge_point_id,
                            raw_message=original,
                        )
                        return False
                    if 0 < direct_current_amp < _DEFAULT_CMA_MIN_CURRENT_AMP or direct_current_amp > max_current_amp:
                        _emit_violation(
                            event_type=SecurityEventType.CHARGE_MANIPULATION_ATTACK,
                            description="SetChargingProfile.current out of strict bounds",
                            command=action,
                            attempted_value=direct_current_amp,
                            charge_point_id=charge_point_id,
                            raw_message=original,
                        )
                        return False

                if "duration" in schedule and schedule.get("duration") is None:
                    _emit_violation(
                        event_type=SecurityEventType.PROFILE_TAMPERING,
                        description="chargingSchedule.duration must be non-null",
                        command=action,
                        attempted_value=schedule.get("duration"),
                        charge_point_id=charge_point_id,
                        raw_message=original,
                    )
                    return False
                duration = schedule.get("duration")
                if duration is not None:
                    duration_value = _to_float(duration)
                    if duration_value is None or duration_value < 0 or duration_value > _MAX_SESSION_DURATION_SECONDS:
                        _emit_violation(
                            event_type=SecurityEventType.PROFILE_TAMPERING,
                            description="Unrealistic charging profile duration (>24h) or invalid duration",
                            command=action,
                            attempted_value=duration,
                            charge_point_id=charge_point_id,
                            raw_message=original,
                        )
                        return False

                periods = schedule.get("chargingSchedulePeriod")
                if periods is None or not isinstance(periods, list) or not periods:
                    _emit_violation(
                        event_type=SecurityEventType.PROFILE_TAMPERING,
                        description="chargingSchedulePeriod must be a non-empty list",
                        command=action,
                        attempted_value=periods,
                        charge_point_id=charge_point_id,
                        raw_message=original,
                    )
                    return False
                if len(periods) > _MAX_SCHEDULE_PERIODS:
                    _emit_violation(
                        event_type=SecurityEventType.PROFILE_TAMPERING,
                        description="chargingSchedulePeriod exceeds allowed maximum entries",
                        command=action,
                        attempted_value=len(periods),
                        charge_point_id=charge_point_id,
                        raw_message=original,
                    )
                    return False

                # Bound list size makes this loop effectively O(1) worst-case.
                last_start_period = -1
                for period in periods:
                    if not isinstance(period, dict):
                        _emit_violation(
                            event_type=SecurityEventType.PROFILE_TAMPERING,
                            description="chargingSchedulePeriod entries must be objects",
                            command=action,
                            attempted_value=period,
                            charge_point_id=charge_point_id,
                            raw_message=original,
                        )
                        return False

                    start_period_raw = period.get("startPeriod")
                    start_period = _to_int(start_period_raw)
                    if start_period is None or start_period < 0 or start_period > _MAX_SESSION_DURATION_SECONDS:
                        _emit_violation(
                            event_type=SecurityEventType.PROFILE_TAMPERING,
                            description="Invalid startPeriod in chargingSchedulePeriod",
                            command=action,
                            attempted_value=start_period_raw,
                            charge_point_id=charge_point_id,
                            raw_message=original,
                        )
                        return False
                    if start_period <= last_start_period:
                        _emit_violation(
                            event_type=SecurityEventType.PROFILE_TAMPERING,
                            description="chargingSchedulePeriod startPeriod must be strictly increasing",
                            command=action,
                            attempted_value={"startPeriod": start_period},
                            charge_point_id=charge_point_id,
                            raw_message=original,
                        )
                        return False
                    last_start_period = start_period

                    current_value = period.get("limit")
                    if current_value is None:
                        _emit_violation(
                            event_type=SecurityEventType.CHARGE_MANIPULATION_ATTACK,
                            description="Null current limit in chargingSchedulePeriod",
                            command=action,
                            attempted_value=current_value,
                            charge_point_id=charge_point_id,
                            raw_message=original,
                        )
                        return False
                    current_amp = _to_float(current_value)
                    if current_amp is None:
                        _emit_violation(
                            event_type=SecurityEventType.CHARGE_MANIPULATION_ATTACK,
                            description="Current limit must be numeric",
                            command=action,
                            attempted_value=current_value,
                            charge_point_id=charge_point_id,
                            raw_message=original,
                        )
                        return False
                    if current_amp < 0:
                        _emit_violation(
                            event_type=SecurityEventType.CHARGE_MANIPULATION_ATTACK,
                            description="Negative current limit rejected",
                            command=action,
                            attempted_value=current_amp,
                            charge_point_id=charge_point_id,
                            raw_message=original,
                        )
                        return False
                    if 0 < current_amp < _DEFAULT_CMA_MIN_CURRENT_AMP or current_amp > max_current_amp:
                        _emit_violation(
                            event_type=SecurityEventType.CHARGE_MANIPULATION_ATTACK,
                            description="SetChargingProfile.current out of strict bounds",
                            command=action,
                            attempted_value=current_amp,
                            charge_point_id=charge_point_id,
                            raw_message=original,
                        )
                        return False

            if action == OcppAction.CHANGE_CONFIGURATION.value:
                if not _check_sliding_limit(charge_point_id, action, _DEFAULT_CONFIG_CHANGES_PER_5MIN, 300):
                    _emit_violation(
                        event_type=SecurityEventType.PROFILE_TAMPERING,
                        description="Rapid sequential ChangeConfiguration attempts detected",
                        command=action,
                        attempted_value={"rate_limit_per_5min": _DEFAULT_CONFIG_CHANGES_PER_5MIN},
                        charge_point_id=charge_point_id,
                        raw_message=original,
                    )
                    return False

                key = payload.get("key")
                attempted_value = payload.get("value")
                if key is None or attempted_value is None:
                    _emit_violation(
                        event_type=SecurityEventType.CHARGE_MANIPULATION_ATTACK,
                        description="ChangeConfiguration key/value must be non-null",
                        command=action,
                        attempted_value={"key": key, "value": attempted_value},
                        charge_point_id=charge_point_id,
                        raw_message=original,
                    )
                    return False
                if not isinstance(key, str) or not key.strip():
                    _emit_violation(
                        event_type=SecurityEventType.CHARGE_MANIPULATION_ATTACK,
                        description="ChangeConfiguration key must be non-empty string",
                        command=action,
                        attempted_value={"key": key, "value": attempted_value},
                        charge_point_id=charge_point_id,
                        raw_message=original,
                    )
                    return False

                normalized_key = key.strip().upper()
                if normalized_key in _POWER_DELIVERY_CONFIG_KEYS:
                    value_numeric = _to_float(attempted_value)
                    if value_numeric is None:
                        _emit_violation(
                            event_type=SecurityEventType.CHARGE_MANIPULATION_ATTACK,
                            description="Power-delivery configuration value must be numeric",
                            command=action,
                            attempted_value={"key": key, "value": attempted_value},
                            charge_point_id=charge_point_id,
                            raw_message=original,
                        )
                        return False
                    if value_numeric < 0:
                        _emit_violation(
                            event_type=SecurityEventType.CHARGE_MANIPULATION_ATTACK,
                            description="Negative power-delivery configuration value rejected",
                            command=action,
                            attempted_value={"key": key, "value": value_numeric},
                            charge_point_id=charge_point_id,
                            raw_message=original,
                        )
                        return False
                    if 0 < value_numeric < _DEFAULT_CMA_MIN_CURRENT_AMP:
                        _emit_violation(
                            event_type=SecurityEventType.CHARGE_MANIPULATION_ATTACK,
                            description="ChangeConfiguration value below minimum current bound",
                            command=action,
                            attempted_value={"key": key, "value": value_numeric},
                            charge_point_id=charge_point_id,
                            raw_message=original,
                        )
                        return False
                    if value_numeric > _DEFAULT_CMA_MAX_CURRENT_DC_AMP:
                        _emit_violation(
                            event_type=SecurityEventType.CHARGE_MANIPULATION_ATTACK,
                            description="ChangeConfiguration value exceeds safe current bound",
                            command=action,
                            attempted_value={"key": key, "value": value_numeric},
                            charge_point_id=charge_point_id,
                            raw_message=original,
                        )
                        return False

                    if normalized_key == "CHARGEPROFILEMAXSTACKLEVEL":
                        stack_value = _to_int(attempted_value)
                        if stack_value is None or stack_value < 0 or stack_value > _DEFAULT_PROFILE_STACK_LEVEL_LIMIT:
                            _emit_violation(
                                event_type=SecurityEventType.PROFILE_TAMPERING,
                                description="ChangeConfiguration stack level integrity violation",
                                command=action,
                                attempted_value={"key": key, "value": attempted_value},
                                charge_point_id=charge_point_id,
                                raw_message=original,
                            )
                            return False

        if message_type_id == 3:
            if len(parsed) < 3 or not isinstance(parsed[2], dict):
                _log_schema_violation(connection_context, "CALLRESULT requires object payload", original)
                return False

        if message_type_id == 4:
            if len(parsed) < 5:
                _log_schema_violation(connection_context, "CALLERROR requires 5 elements", original)
                return False
            if not isinstance(parsed[2], str) or not isinstance(parsed[3], str) or not isinstance(parsed[4], dict):
                _log_schema_violation(connection_context, "CALLERROR fields must be [str, str, object]", original)
                return False

        return True
    except Exception:
        logger.exception("Unhandled validation error")
        _log_schema_violation(connection_context, "Unhandled exception in validator", message)
        return False


def _log_schema_violation(connection_context: Dict[str, str], description: str, raw_message: Any) -> None:
    charge_point_id = connection_context.get("charge_point_id", "UNKNOWN")
    insert_security_event(
        {
            "charge_point_id": charge_point_id,
            "event_type": SecurityEventType.OCPP_SCHEMA_VIOLATION.value,
            "severity": 5,
            "description": description,
            "raw_message": raw_message,
        }
    )


ocpp_state_machine = OcppStateMachine()
ocpp_rate_limiter = OcppRateLimiter()
behavioral_anomaly_detector = BehavioralAnomalyDetector()
