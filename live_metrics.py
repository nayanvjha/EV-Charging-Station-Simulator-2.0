from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional, TypedDict


class LiveMetrics(TypedDict):
    power_kw: float
    energy_kwh: float
    soc_percent: float
    timestamp: datetime


_LIVE_METRICS: Dict[str, LiveMetrics] = {}


def record_live_metrics(
    station_id: str,
    power_kw: float,
    energy_kwh: float,
    soc_percent: float,
    timestamp: datetime,
) -> None:
    _LIVE_METRICS[station_id] = {
        "power_kw": float(power_kw),
        "energy_kwh": float(energy_kwh),
        "soc_percent": float(soc_percent),
        "timestamp": timestamp,
    }


def get_live_metrics(station_id: str) -> Optional[LiveMetrics]:
    data = _LIVE_METRICS.get(station_id)
    if not data:
        return None
    return dict(data)


def get_live_metrics_snapshot() -> Dict[str, LiveMetrics]:
    return {station_id: dict(metrics) for station_id, metrics in _LIVE_METRICS.items()}


def reset_live_metrics() -> None:
    _LIVE_METRICS.clear()


__all__ = [
    "LiveMetrics",
    "record_live_metrics",
    "get_live_metrics",
    "get_live_metrics_snapshot",
    "reset_live_metrics",
]
