"""
Prometheus metrics for the EV Charging Station Simulator.

Tracks:
- stations_total: Total number of simulated stations (Gauge)
- stations_active: Currently running stations (Gauge)
- transactions_total: Total transactions started (Counter)
- energy_dispensed_kwh: Total energy delivered across all stations (Counter)
"""

from prometheus_client import Counter, Gauge, generate_latest, REGISTRY

# Define metrics
stations_total = Gauge(
    "stations_total",
    "Total number of simulated stations",
)

stations_active = Gauge(
    "stations_active",
    "Currently running stations",
)

transactions_total = Counter(
    "transactions_total",
    "Total transactions started",
)

energy_dispensed_kwh = Counter(
    "energy_dispensed_kwh",
    "Total energy delivered across all stations in kWh",
)

meter_values_total = Counter(
    "meter_values_total",
    "Total meter value updates across all stations",
)


def get_metrics_text():
    """Return metrics in Prometheus text format."""
    return generate_latest(REGISTRY).decode("utf-8")


def record_station_started():
    """Record when a station starts."""
    stations_total.inc()
    stations_active.inc()


def record_station_stopped():
    """Record when a station stops."""
    stations_active.dec()


def record_transaction_started():
    """Record when a transaction starts."""
    transactions_total.inc()


def record_energy_dispensed(kwh: float):
    """Record energy dispensed in kWh."""
    energy_dispensed_kwh.inc(kwh)


def record_meter_value():
    """Record a meter value update."""
    meter_values_total.inc()


def set_stations_active(count: int):
    """Set the current number of active stations."""
    stations_active._value.set(count)


def set_stations_total(count: int):
    """Set the total number of stations."""
    stations_total._value.set(count)
