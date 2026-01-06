from dataclasses import dataclass, field
from typing import List, Dict


@dataclass
class StationProfile:
    name: str
    heartbeat_interval: int = 60

    idle_min: int = 30
    idle_max: int = 120

    energy_step_min: int = 50
    energy_step_max: int = 150

    sample_interval_min: int = 10
    sample_interval_max: int = 20

    enable_transactions: bool = True

    offline_probability: float = 0.0
    offline_duration: int = 0

    id_tags: List[str] = field(
        default_factory=lambda: ["ABC123", "TAG001", "USER42"]
    )

    # Smart charging parameters
    charge_if_price_below: float = 100.0  # Price threshold (don't charge if above)
    max_energy_kwh: float = 30.0  # Max energy per charging session
    allow_peak: bool = True  # Allow charging during peak hours (8-18)
    peak_hours: tuple = (8, 18)  # Define peak hours (start, end)


DEFAULT_PROFILES: Dict[str, StationProfile] = {
    "default": StationProfile(
        name="default",
        charge_if_price_below=25.0,
        max_energy_kwh=30.0,
    ),

    "busy": StationProfile(
        name="busy",
        idle_min=5,
        idle_max=20,
        energy_step_min=80,
        energy_step_max=220,
        charge_if_price_below=30.0,
        max_energy_kwh=40.0,
    ),

    "idle": StationProfile(
        name="idle",
        idle_min=180,
        idle_max=600,
        enable_transactions=True,
        charge_if_price_below=18.0,
        max_energy_kwh=20.0,
        allow_peak=False,  # Don't charge during peak hours
    ),

    "no-transactions": StationProfile(
        name="no-transactions",
        enable_transactions=False,
    ),

    "flaky": StationProfile(
        name="flaky",
        idle_min=20,
        idle_max=60,
        offline_probability=0.1,   # 10% chance to go offline
        offline_duration=30,
        charge_if_price_below=20.0,
        max_energy_kwh=25.0,
    ),
}
