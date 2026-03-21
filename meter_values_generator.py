from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import inspect
from typing import Callable, List, Optional

from ocpp.v16 import call


@dataclass(frozen=True)
class MeterSeries:
    """Deterministic meter series for a session."""

    timestamps: List[datetime]
    values_wh: List[float]


def generate_meter_values(
    start_time: datetime,
    end_time: datetime,
    total_energy_kwh: float,
    reporting_interval_seconds: int,
    limit_watts_provider: Optional[Callable[[datetime], Optional[float]]] = None,
) -> MeterSeries:
    """
    Generate deterministic, monotonic MeterValues for a charging session.
    """
    if end_time <= start_time:
        raise ValueError("end_time must be after start_time")
    if reporting_interval_seconds <= 0:
        raise ValueError("reporting_interval_seconds must be positive")
    if total_energy_kwh < 0:
        raise ValueError("total_energy_kwh must be non-negative")

    total_duration = (end_time - start_time).total_seconds()
    if total_duration <= 0:
        raise ValueError("session duration must be positive")

    total_energy_wh = total_energy_kwh * 1000.0

    if limit_watts_provider is None:
        intervals = int(total_duration // reporting_interval_seconds)
        if intervals <= 0:
            raise ValueError("session duration shorter than reporting interval")

        timestamps: List[datetime] = []
        values_wh: List[float] = []

        for i in range(1, intervals + 1):
            timestamps.append(start_time + timedelta(seconds=i * reporting_interval_seconds))
            values_wh.append(0.0)

        remainder_seconds = total_duration - (intervals * reporting_interval_seconds)
        if remainder_seconds > 0:
            timestamps.append(end_time)
            values_wh.append(0.0)

        step_wh = total_energy_wh / len(values_wh) if values_wh else 0.0
        cumulative = 0.0
        increments: List[float] = []

        for idx in range(len(values_wh)):
            increment = step_wh
            if increment < 0:
                raise AssertionError("negative energy increment")
            cumulative += increment
            values_wh[idx] = cumulative
            increments.append(increment)

        if values_wh:
            values_wh[-1] = total_energy_wh

        _assert_series(
            start_time,
            end_time,
            values_wh,
            timestamps,
            total_energy_wh,
            increments,
            allow_extension=False,
        )

        return MeterSeries(timestamps=timestamps, values_wh=values_wh)

    timestamps: List[datetime] = []
    values_wh: List[float] = []
    increments: List[float] = []

    cursor = start_time + timedelta(seconds=reporting_interval_seconds)
    if cursor > end_time:
        cursor = end_time

    cumulative = 0.0
    while cumulative < total_energy_wh or not timestamps:
        limit_w = limit_watts_provider(cursor)
        if limit_w is not None:
            if limit_w <= 0:
                raise ValueError("power limit must be positive")
            max_wh = limit_w * (reporting_interval_seconds / 3600.0)
        else:
            max_wh = None

        remaining = total_energy_wh - cumulative
        increment = remaining if max_wh is None else min(remaining, max_wh)
        if increment < 0:
            raise AssertionError("negative energy increment")
        cumulative += increment
        timestamps.append(cursor)
        values_wh.append(cumulative)
        increments.append(increment)

        if cumulative >= total_energy_wh:
            break

        cursor = cursor + timedelta(seconds=reporting_interval_seconds)

    _assert_series(
        start_time,
        end_time,
        values_wh,
        timestamps,
        total_energy_wh,
        increments,
        allow_extension=True,
    )

    return MeterSeries(timestamps=timestamps, values_wh=values_wh)


_ORIGINAL_METER_VALUES = getattr(call, "MeterValues", None) or getattr(call, "MeterValuesPayload", None)
if _ORIGINAL_METER_VALUES is None:
    raise RuntimeError("OCPP MeterValues request class not found in ocpp.v16.call")


def _assert_meter_values_caller() -> None:
    for frame_info in inspect.stack()[1:]:
        module = inspect.getmodule(frame_info.frame)
        if module is None:
            continue
        if module.__name__ == __name__:
            return
        break
    raise RuntimeError("Direct MeterValues construction is forbidden. Use build_meter_values.")


class GuardedMeterValues(_ORIGINAL_METER_VALUES):
    def __init__(self, *args, **kwargs):
        _assert_meter_values_caller()
        super().__init__(*args, **kwargs)


GuardedMeterValues.__name__ = _ORIGINAL_METER_VALUES.__name__
GuardedMeterValues.__qualname__ = _ORIGINAL_METER_VALUES.__qualname__


if hasattr(call, "MeterValues"):
    call.MeterValues = GuardedMeterValues
else:
    call.MeterValuesPayload = GuardedMeterValues


def build_meter_values(
    connector_id: int,
    transaction_id: int,
    energy_wh: float,
    timestamp: datetime,
) -> object:
    _assert_meter_values_caller()
    meter_values_cls = getattr(call, "MeterValues", None) or getattr(call, "MeterValuesPayload")
    return meter_values_cls(
        connector_id=connector_id,
        transaction_id=transaction_id,
        meter_value=[
            {
                "timestamp": timestamp.isoformat(),
                "sampled_value": [
                    {
                        "value": str(energy_wh),
                        "measurand": "Energy.Active.Import.Register",
                    }
                ],
            }
        ],
    )


def build_meter_values_with_power_soc(
    connector_id: int,
    transaction_id: int,
    energy_wh: float,
    power_kw: float,
    soc_percent: float,
    timestamp: datetime,
) -> object:
    _assert_meter_values_caller()
    meter_values_cls = getattr(call, "MeterValues", None) or getattr(call, "MeterValuesPayload")
    return meter_values_cls(
        connector_id=connector_id,
        transaction_id=transaction_id,
        meter_value=[
            {
                "timestamp": timestamp.isoformat(),
                "sampled_value": [
                    {
                        "value": str(energy_wh),
                        "measurand": "Energy.Active.Import.Register",
                    },
                    {
                        "value": str(power_kw * 1000.0),
                        "measurand": "Power.Active.Import",
                        "unit": "W",
                    },
                    {
                        "value": str(soc_percent),
                        "measurand": "SoC",
                        "unit": "Percent",
                    },
                ],
            }
        ],
    )


def _assert_series(
    start_time: datetime,
    end_time: datetime,
    values_wh: List[float],
    timestamps: List[datetime],
    total_energy_wh: float,
    increments: List[float],
    allow_extension: bool,
) -> None:
    if len(values_wh) != len(timestamps):
        raise AssertionError("values and timestamps length mismatch")

    if not values_wh:
        raise AssertionError("no meter values generated")

    if timestamps[0] <= start_time:
        raise AssertionError("first meter value must be after start_time")

    if not allow_extension and timestamps[-1] > end_time:
        raise AssertionError("meter values exceed end_time")

    for idx in range(1, len(values_wh)):
        if values_wh[idx] < values_wh[idx - 1]:
            raise AssertionError("meter values must be monotonic")

    if abs(values_wh[-1] - total_energy_wh) > 1e-6:
        raise AssertionError("cumulative energy drift")

    if abs(sum(increments) - total_energy_wh) > 1e-6:
        raise AssertionError("increment sum drift")
