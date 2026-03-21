from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from replay_mode import assert_real_csv_entry_active, is_real_csv_mode, is_strict_mode


@dataclass(frozen=True)
class SessionValidationResult:
    session_uid: str
    expected_duration_seconds: float
    actual_duration_seconds: float
    expected_total_energy_kwh: float
    actual_total_energy_kwh: float
    delta_energy_kwh: float
    abs_delta_energy_kwh: float
    delta_energy_percent: Optional[float]
    emitted_meter_values_count: int
    expected_meter_values_count: int
    smart_charging_active: bool
    passed: bool
    reasons: List[str]
    faults: List[str]


@dataclass
class ValidationReport:
    duration_tolerance_seconds: float
    energy_tolerance_kwh: float
    results: Dict[str, SessionValidationResult] = field(default_factory=dict)
    _finalized: bool = field(default=False, init=False, repr=False)

    def record_session(
        self,
        session_uid: str,
        expected_duration_seconds: float,
        actual_duration_seconds: float,
        expected_total_energy_kwh: float,
        actual_total_energy_kwh: float,
        emitted_meter_values_count: int,
        smart_charging_active: bool,
        reporting_interval_seconds: int,
        faults: Optional[List[str]] = None,
    ) -> SessionValidationResult:
        reasons: List[str] = []
        faults_list = list(faults or [])
        expected_snapshot = expected_total_energy_kwh
        actual_snapshot = actual_total_energy_kwh

        delta_energy_kwh = actual_total_energy_kwh - expected_total_energy_kwh
        abs_delta_energy_kwh = abs(delta_energy_kwh)
        delta_energy_percent = None
        if expected_total_energy_kwh != 0:
            delta_energy_percent = (delta_energy_kwh / expected_total_energy_kwh) * 100.0

        if is_strict_mode():
            if actual_duration_seconds != expected_duration_seconds:
                reasons.append("duration mismatch")
            if delta_energy_kwh != 0:
                reasons.append("energy mismatch")
        else:
            if smart_charging_active:
                if actual_duration_seconds + self.duration_tolerance_seconds < expected_duration_seconds:
                    reasons.append("duration shorter than expected under smart charging")
            else:
                if abs(actual_duration_seconds - expected_duration_seconds) > self.duration_tolerance_seconds:
                    reasons.append("duration deviation exceeds tolerance")

            if not is_real_csv_mode():
                if abs(actual_total_energy_kwh - expected_total_energy_kwh) > self.energy_tolerance_kwh:
                    reasons.append(
                        "energy deviation exceeds tolerance"
                    )

        if reporting_interval_seconds <= 0:
            reasons.append("reporting interval must be positive")
            expected_count = 0
        else:
            duration_for_count = (
                expected_duration_seconds
                if is_strict_mode()
                else (actual_duration_seconds if smart_charging_active else expected_duration_seconds)
            )
            if duration_for_count < 0:
                reasons.append("duration must be non-negative")
                expected_count = 0
            else:
                intervals = int(duration_for_count // reporting_interval_seconds)
                remainder = duration_for_count - (intervals * reporting_interval_seconds)
                expected_count = intervals + (1 if remainder > 0 else 0)
                diff = abs(emitted_meter_values_count - expected_count)
                if is_strict_mode():
                    if diff != 0:
                        reasons.append("meter values count mismatch")
                else:
                    if remainder > 0:
                        if diff > 1:
                            reasons.append("meter values count exceeds partial-interval tolerance")
                    else:
                        if diff != 0:
                            reasons.append("meter values count mismatch")

        passed = not reasons
        result = SessionValidationResult(
            session_uid=session_uid,
            expected_duration_seconds=expected_duration_seconds,
            actual_duration_seconds=actual_duration_seconds,
            expected_total_energy_kwh=expected_total_energy_kwh,
            actual_total_energy_kwh=actual_total_energy_kwh,
            delta_energy_kwh=delta_energy_kwh,
            abs_delta_energy_kwh=abs_delta_energy_kwh,
            delta_energy_percent=delta_energy_percent,
            emitted_meter_values_count=emitted_meter_values_count,
            expected_meter_values_count=expected_count,
            smart_charging_active=smart_charging_active,
            passed=passed,
            reasons=reasons,
            faults=faults_list,
        )
        self.results[session_uid] = result
        if expected_total_energy_kwh != expected_snapshot or actual_total_energy_kwh != actual_snapshot:
            raise RuntimeError("Validation must not mutate energy values")
        return result

    def to_payload(self) -> List[Dict[str, object]]:
        payload: List[Dict[str, object]] = []
        for session_uid in sorted(self.results.keys()):
            result = self.results[session_uid]
            payload.append(
                {
                    "session_uid": result.session_uid,
                    "expected_duration_seconds": result.expected_duration_seconds,
                    "actual_duration_seconds": result.actual_duration_seconds,
                    "expected_total_energy_kwh": result.expected_total_energy_kwh,
                    "actual_total_energy_kwh": result.actual_total_energy_kwh,
                    "delta_energy_kwh": result.delta_energy_kwh,
                    "abs_delta_energy_kwh": result.abs_delta_energy_kwh,
                    "delta_energy_percent": result.delta_energy_percent,
                    "emitted_meter_values_count": result.emitted_meter_values_count,
                    "expected_meter_values_count": result.expected_meter_values_count,
                    "smart_charging_active": result.smart_charging_active,
                    "passed": result.passed,
                    "reasons": list(result.reasons),
                    "faults": list(result.faults),
                }
            )
        return payload

    def write_json(self, path: str) -> None:
        Path(path).write_text(
            json.dumps(self.to_payload(), indent=2),
            encoding="utf-8",
        )

    def assert_all_passed(self) -> None:
        failures = [result for result in self.results.values() if not result.passed]
        if failures:
            raise RuntimeError("Validation failed for one or more sessions")

    def raise_on_failure(self) -> None:
        self.assert_all_passed()

    def finalize(self) -> None:
        if self._finalized:
            raise RuntimeError("ValidationReport finalized more than once")
        if is_real_csv_mode():
            assert_real_csv_entry_active()
        if is_strict_mode():
            self.raise_on_failure()
        self._finalized = True

    def finalize_with_warnings(self) -> None:
        if self._finalized:
            raise RuntimeError("ValidationReport finalized more than once")
        if is_real_csv_mode():
            assert_real_csv_entry_active()
        self._finalized = True

    def passed(self) -> bool:
        if not self._finalized:
            return False
        return all(result.passed for result in self.results.values())


_ACTIVE_REPORT: Optional[ValidationReport] = None


def set_active_report(report: Optional[ValidationReport]) -> None:
    global _ACTIVE_REPORT
    _ACTIVE_REPORT = report


def get_active_report() -> Optional[ValidationReport]:
    return _ACTIVE_REPORT