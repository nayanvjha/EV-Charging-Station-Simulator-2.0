from dataclasses import dataclass
from typing import List, Dict, Tuple


@dataclass
class StationProfile:
    name: str
    heartbeat_interval: int

    idle_min: int
    idle_max: int

    energy_step_min: int
    energy_step_max: int

    sample_interval_min: int
    sample_interval_max: int

    enable_transactions: bool

    offline_probability: float
    offline_duration: int

    id_tags: List[str]

    # Smart charging parameters
    charge_if_price_below: float  # Price threshold (don't charge if above)
    max_energy_kwh: float  # Max energy per charging session
    allow_peak: bool  # Allow charging during peak hours (8-18)
    peak_hours: Tuple[int, int]  # Define peak hours (start, end)

    # Battery/physics parameters (profile-authoritative)
    battery_capacity_kwh: float
    initial_soc_kwh: float
    temperature_c: float
    max_charge_power_kw: float
    tapering_enabled: bool

    def __post_init__(self) -> None:
        required_fields = (
            "heartbeat_interval",
            "idle_min",
            "idle_max",
            "energy_step_min",
            "energy_step_max",
            "sample_interval_min",
            "sample_interval_max",
            "enable_transactions",
            "offline_probability",
            "offline_duration",
            "id_tags",
            "charge_if_price_below",
            "max_energy_kwh",
            "allow_peak",
            "peak_hours",
            "battery_capacity_kwh",
            "initial_soc_kwh",
            "temperature_c",
            "max_charge_power_kw",
            "tapering_enabled",
        )
        for field_name in required_fields:
            if getattr(self, field_name) is None:
                raise RuntimeError(f"StationProfile missing required field: {field_name}")


DEFAULT_PROFILES: Dict[str, StationProfile] = {
    "default": StationProfile(
        name="default",
        heartbeat_interval=60,
        idle_min=30,
        idle_max=120,
        energy_step_min=200,
        energy_step_max=700,
        sample_interval_min=10,
        sample_interval_max=20,
        enable_transactions=True,
        offline_probability=0.02,
        offline_duration=120,
        id_tags=["ABC123", "DEF456"],
        charge_if_price_below=25.0,
        max_energy_kwh=30.0,
        allow_peak=True,
        peak_hours=(8, 18),
        battery_capacity_kwh=60.0,
        initial_soc_kwh=10.0,
        temperature_c=25.0,
        max_charge_power_kw=11.0,
        tapering_enabled=True,
    ),
    "busy": StationProfile(
        name="busy",
        heartbeat_interval=30,
        idle_min=5,
        idle_max=30,
        energy_step_min=500,
        energy_step_max=1200,
        sample_interval_min=5,
        sample_interval_max=10,
        enable_transactions=True,
        offline_probability=0.01,
        offline_duration=60,
        id_tags=["BUSY1", "BUSY2"],
        charge_if_price_below=30.0,
        max_energy_kwh=40.0,
        allow_peak=True,
        peak_hours=(8, 18),
        battery_capacity_kwh=75.0,
        initial_soc_kwh=15.0,
        temperature_c=25.0,
        max_charge_power_kw=22.0,
        tapering_enabled=True,
    ),
    "idle": StationProfile(
        name="idle",
        heartbeat_interval=120,
        idle_min=300,
        idle_max=900,
        energy_step_min=100,
        energy_step_max=300,
        sample_interval_min=15,
        sample_interval_max=30,
        enable_transactions=True,
        offline_probability=0.0,
        offline_duration=0,
        id_tags=["IDLE1"],
        charge_if_price_below=18.0,
        max_energy_kwh=20.0,
        allow_peak=False,
        peak_hours=(8, 18),
        battery_capacity_kwh=50.0,
        initial_soc_kwh=8.0,
        temperature_c=25.0,
        max_charge_power_kw=7.4,
        tapering_enabled=True,
    ),
    "no-transactions": StationProfile(
        name="no-transactions",
        heartbeat_interval=60,
        idle_min=60,
        idle_max=180,
        energy_step_min=0,
        energy_step_max=0,
        sample_interval_min=30,
        sample_interval_max=60,
        enable_transactions=False,
        offline_probability=0.0,
        offline_duration=0,
        id_tags=["NONE"],
        charge_if_price_below=0.0,
        max_energy_kwh=0.0,
        allow_peak=True,
        peak_hours=(8, 18),
        battery_capacity_kwh=60.0,
        initial_soc_kwh=10.0,
        temperature_c=25.0,
        max_charge_power_kw=11.0,
        tapering_enabled=True,
    ),
    "flaky": StationProfile(
        name="flaky",
        heartbeat_interval=90,
        idle_min=30,
        idle_max=180,
        energy_step_min=200,
        energy_step_max=600,
        sample_interval_min=10,
        sample_interval_max=25,
        enable_transactions=True,
        offline_probability=0.35,
        offline_duration=300,
        id_tags=["FLAKY1", "FLAKY2"],
        charge_if_price_below=22.0,
        max_energy_kwh=25.0,
        allow_peak=False,
        peak_hours=(8, 18),
        battery_capacity_kwh=55.0,
        initial_soc_kwh=12.0,
        temperature_c=25.0,
        max_charge_power_kw=11.0,
        tapering_enabled=True,
    ),
}
