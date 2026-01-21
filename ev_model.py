from typing import Callable, Optional


class BatteryModel:
    def __init__(
        self,
        capacity_kwh: float = 60.0,
        soc_kwh: float = 10.0,
        temperature_c: float = 25.0,
        max_charge_power_kw: float = 11.0,
        tapering_enabled: bool = True,
        soc_curve: Optional[Callable[[float], float]] = None,
    ) -> None:
        self.capacity_kwh = max(0.1, float(capacity_kwh))
        self.soc_kwh = max(0.0, min(float(soc_kwh), self.capacity_kwh))
        self.temperature_c = float(temperature_c)
        self.max_charge_power_kw = max(0.1, float(max_charge_power_kw))
        self.tapering_enabled = bool(tapering_enabled)
        self.soc_curve = soc_curve

        self.last_power_kw = 0.0
        self.last_reason: Optional[str] = None
        self.last_tapered = False

    def soc_percent(self) -> float:
        return (self.soc_kwh / self.capacity_kwh) * 100.0

    def _temperature_factor(self) -> float:
        if self.temperature_c < 0:
            return 0.5
        if self.temperature_c > 40:
            return 0.8
        return 1.0

    def _taper_factor(self) -> float:
        if not self.tapering_enabled:
            return 1.0
        soc = self.soc_percent()
        if soc <= 80:
            return 1.0
        if soc <= 90:
            return 0.5
        return 0.25

    def _curve_limit_kw(self) -> Optional[float]:
        if not self.soc_curve:
            return None
        try:
            return max(0.0, float(self.soc_curve(self.soc_percent())))
        except Exception:
            return None

    def step_charge(self, delta_seconds: float) -> float:
        self.last_reason = None
        self.last_tapered = False
        self.last_power_kw = 0.0

        if self.soc_kwh >= self.capacity_kwh:
            self.last_reason = "Battery full"
            return 0.0

        hours = max(0.0, float(delta_seconds)) / 3600.0
        if hours <= 0:
            return 0.0

        power_kw = self.max_charge_power_kw

        curve_limit = self._curve_limit_kw()
        if curve_limit is not None:
            if curve_limit < power_kw:
                self.last_reason = f"SOC curve limit {curve_limit:.1f} kW"
                self.last_tapered = True
            power_kw = min(power_kw, curve_limit)
        else:
            taper = self._taper_factor()
            if taper < 1.0:
                self.last_reason = f"SOC > 80% – tapering to {power_kw * taper:.1f} kW"
                self.last_tapered = True
            power_kw *= taper

        temp_factor = self._temperature_factor()
        if temp_factor < 1.0:
            self.last_reason = f"Temperature derate to {power_kw * temp_factor:.1f} kW"
            self.last_tapered = True
            power_kw *= temp_factor

        accepted_kwh = power_kw * hours
        if self.soc_kwh + accepted_kwh > self.capacity_kwh:
            accepted_kwh = self.capacity_kwh - self.soc_kwh

        self.soc_kwh += accepted_kwh
        self.last_power_kw = power_kw

        return accepted_kwh

    def update_profile(
        self,
        capacity_kwh: Optional[float] = None,
        soc_kwh: Optional[float] = None,
        temperature_c: Optional[float] = None,
        max_charge_power_kw: Optional[float] = None,
        tapering_enabled: Optional[bool] = None,
    ) -> None:
        if capacity_kwh is not None:
            self.capacity_kwh = max(0.1, float(capacity_kwh))
        if soc_kwh is not None:
            self.soc_kwh = max(0.0, min(float(soc_kwh), self.capacity_kwh))
        if temperature_c is not None:
            self.temperature_c = float(temperature_c)
        if max_charge_power_kw is not None:
            self.max_charge_power_kw = max(0.1, float(max_charge_power_kw))
        if tapering_enabled is not None:
            self.tapering_enabled = bool(tapering_enabled)
