from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable, Dict, Mapping, Optional, TypedDict

from accounting import ensure_session, finalize_session, update_session_energy
from state_version import increment_state_version
from replay_mode import assert_real_csv_entry_active, is_real_csv_mode, is_strict_mode
from session_planner import SessionPlan
from station import SimulatedChargePoint


@dataclass(frozen=True)
class ChargingLoopConfig:
    tick_interval_sec: float = 1.0
    connector_id: int = 1
    id_tag: str = "REAL_CSV"
    battery_capacity_kwh: Decimal = Decimal("60")
    initial_soc_kwh: Decimal = Decimal("10")
    temperature_c: Decimal = Decimal("25")
    max_charge_power_kw: Decimal = Decimal("11")
    tapering_enabled: bool = True
    meter_start_wh: int = 0
    session_id: Optional[str] = None
    policy_id: Optional[str] = None
    policy_params: Optional[Mapping[str, object]] = None
    policy_limit_kw_provider: Optional[
        Callable[[datetime, "ChargingLoopState"], Optional[Decimal]]
    ] = None


@dataclass(frozen=True)
class ChargingLoopResult:
    station_id: str
    session_id: str
    transaction_id: int
    energy_kwh: Decimal
    start_time: datetime
    stop_time: datetime
    ticks_emitted: int


@dataclass(frozen=True)
class ChargingLoopState:
    station_id: str
    session_id: str
    replay_time: datetime
    elapsed_sec: int
    cumulative_energy_kwh: Decimal
    remaining_energy_kwh: Decimal
    avg_power_kw: Decimal


class ChargingLoopValidationSnapshot(TypedDict):
    session_uid: str
    expected_duration_seconds: float
    actual_duration_seconds: float
    expected_total_energy_kwh: float
    actual_total_energy_kwh: float
    emitted_meter_values_count: int
    smart_charging_active: bool


class DeterministicBattery:
    def __init__(
        self,
        capacity_kwh: Decimal,
        soc_kwh: Decimal,
        temperature_c: Decimal,
        max_charge_power_kw: Decimal,
        tapering_enabled: bool,
    ) -> None:
        capacity = capacity_kwh if capacity_kwh > 0 else Decimal("0.1")
        soc = soc_kwh
        if soc < 0:
            soc = Decimal("0")
        if soc > capacity:
            soc = capacity
        self.capacity_kwh = capacity
        self.soc_kwh = soc
        self.temperature_c = temperature_c
        self.max_charge_power_kw = max_charge_power_kw if max_charge_power_kw > 0 else Decimal("0.1")
        self.tapering_enabled = tapering_enabled

    def _temperature_factor(self) -> Decimal:
        if self.temperature_c < Decimal("0"):
            return Decimal("0.5")
        if self.temperature_c > Decimal("40"):
            return Decimal("0.8")
        return Decimal("1")

    def _taper_factor(self) -> Decimal:
        if not self.tapering_enabled:
            return Decimal("1")
        soc = self.soc_percent()
        if soc <= Decimal("80"):
            return Decimal("1")
        if soc <= Decimal("90"):
            return Decimal("0.5")
        return Decimal("0.25")

    def allowed_power_kw(self, external_limit_kw: Optional[Decimal]) -> Decimal:
        power = self.max_charge_power_kw
        taper = self._taper_factor()
        if taper < Decimal("1"):
            power *= taper
        temp_factor = self._temperature_factor()
        if temp_factor < Decimal("1"):
            power *= temp_factor
        if external_limit_kw is not None and external_limit_kw > 0:
            if external_limit_kw < power:
                power = external_limit_kw
        if power < Decimal("0"):
            return Decimal("0")
        return power

    def accept_energy(self, energy_kwh: Decimal) -> Decimal:
        if energy_kwh <= 0:
            return Decimal("0")
        remaining = self.capacity_kwh - self.soc_kwh
        if remaining <= 0:
            return Decimal("0")
        accepted = energy_kwh if energy_kwh <= remaining else remaining
        self.soc_kwh += accepted
        return accepted

    def soc_percent(self) -> Decimal:
        if self.capacity_kwh <= 0:
            return Decimal("0")
        return (self.soc_kwh / self.capacity_kwh) * Decimal("100")


class ChargingLoop:
    def __init__(
        self,
        plan: SessionPlan,
        chargepoint: SimulatedChargePoint,
        config: Optional[ChargingLoopConfig] = None,
    ) -> None:
        self.plan = plan
        self.chargepoint = chargepoint
        self.config = config or ChargingLoopConfig()
        self._assert_real_csv_only()
        if self.config.tick_interval_sec <= 0:
            raise ValueError("tick_interval_sec must be positive")
        self._started = False
        self._completed = False
        self._transaction_id: Optional[int] = None
        self._cumulative_energy_kwh = Decimal("0")
        self._ticks_emitted = 0
        self._actual_start: Optional[datetime] = None
        self._actual_stop: Optional[datetime] = None
        self._policy_limited = False
        self._extension_started = False
        self._battery = DeterministicBattery(
            capacity_kwh=Decimal(self.config.battery_capacity_kwh),
            soc_kwh=Decimal(self.config.initial_soc_kwh),
            temperature_c=Decimal(self.config.temperature_c),
            max_charge_power_kw=Decimal(self.config.max_charge_power_kw),
            tapering_enabled=bool(self.config.tapering_enabled),
        )

    def _assert_real_csv_only(self) -> None:
        if is_strict_mode():
            raise RuntimeError("ChargingLoop is forbidden in STRICT mode")
        if not is_real_csv_mode():
            raise RuntimeError("ChargingLoop runs only in REAL_CSV mode")
        assert_real_csv_entry_active()

    def _session_id(self) -> str:
        if self.config.session_id:
            return self.config.session_id
        return (
            f"{self.plan.station_id}:{self.plan.start_time.isoformat()}:{self.plan.end_time.isoformat()}"
        )

    def _format_decimal(self, value: Decimal, places: str = "0.001") -> str:
        return str(value.quantize(Decimal(places)))

    def _format_policy_params(self) -> str:
        if not self.config.policy_params:
            return "{}"
        items = sorted(self.config.policy_params.items(), key=lambda item: str(item[0]))
        parts = [f"{key}={value}" for key, value in items]
        return "{" + ", ".join(parts) + "}"

    async def on_tick(self, replay_time: datetime) -> bool:
        self._assert_real_csv_only()
        if self._completed:
            return True
        if replay_time < self.plan.start_time:
            return False

        session_id = self._session_id()
        if not self._started:
            self._actual_start = replay_time
            self._transaction_id = await self.chargepoint.start_replay_transaction(
                connector_id=self.config.connector_id,
                id_tag=self.config.id_tag,
                meter_start=self.config.meter_start_wh,
                timestamp=replay_time,
                session_id=session_id,
            )
            self._started = True
            self._last_tick_time = replay_time
            ensure_session(session_id)
            increment_state_version()

        if self._transaction_id is None or self._last_tick_time is None:
            raise RuntimeError("ChargingLoop started without transaction context")

        delta_seconds = (replay_time - self._last_tick_time).total_seconds()
        if delta_seconds <= 0:
            return False

        if replay_time >= self.plan.end_time:
            await self._stop(replay_time)
            return True

        if (not self._extension_started) and replay_time >= self.plan.end_time:
            self._extension_started = True
            self.chargepoint.log(
                "REAL_CSV policy extension started",
                replay_timestamp=replay_time,
            )

        power_kw = Decimal(self.config.max_charge_power_kw)
        total_energy_kwh = Decimal(self.plan.total_energy_kWh)
        remaining_energy = total_energy_kwh - self._cumulative_energy_kwh
        if remaining_energy < Decimal("0"):
            remaining_energy = Decimal("0")

        state = ChargingLoopState(
            station_id=self.plan.station_id,
            session_id=session_id,
            replay_time=replay_time,
            elapsed_sec=int((replay_time - self.plan.start_time).total_seconds()),
            cumulative_energy_kwh=self._cumulative_energy_kwh,
            remaining_energy_kwh=remaining_energy,
            avg_power_kw=power_kw,
        )

        limit_kw = None
        limit_kw_decimal = None
        if self.config.policy_limit_kw_provider is not None:
            limit_kw = self.config.policy_limit_kw_provider(replay_time, state)
            if limit_kw is not None and limit_kw <= 0:
                raise RuntimeError("Policy limit must be positive")
            if limit_kw is not None:
                limit_kw_decimal = Decimal(str(limit_kw))

        effective_power_kw = self._battery.allowed_power_kw(limit_kw_decimal)
        if limit_kw_decimal is not None and limit_kw_decimal < power_kw:
            self._policy_limited = True

        energy_increment = (
            effective_power_kw
            * Decimal(str(delta_seconds))
            / Decimal("3600")
        )
        if energy_increment < 0:
            energy_increment = Decimal("0")
        if energy_increment > remaining_energy:
            energy_increment = remaining_energy

        accepted_energy = self._battery.accept_energy(energy_increment)
        update_session_energy(session_id, accepted_energy)

        if accepted_energy > 0:
            increment_state_version()

        self._cumulative_energy_kwh += accepted_energy
        soc_percent = self._battery.soc_percent().quantize(Decimal("0.01"))
        energy_wh = float(self._cumulative_energy_kwh * Decimal("1000"))

        limit_text = "none" if limit_kw_decimal is None else self._format_decimal(limit_kw_decimal)
        policy_id = self.config.policy_id or "none"
        policy_params = self._format_policy_params()
        self.chargepoint.log(
            "REAL_CSV policy tick "
            f"policy_id={policy_id} params={policy_params} "
            f"limit_kw={limit_text} "
            f"avg_kw={self._format_decimal(power_kw)} "
            f"effective_kw={self._format_decimal(effective_power_kw)} "
            f"energy_kwh={self._format_decimal(self._cumulative_energy_kwh)}",
            replay_timestamp=replay_time,
        )

        await self.chargepoint.emit_replay_meter_values_live(
            connector_id=self.config.connector_id,
            transaction_id=self._transaction_id,
            energy_wh=energy_wh,
            power_kw=float(effective_power_kw),
            soc_percent=float(soc_percent),
            timestamp=replay_time,
        )

        self._ticks_emitted += 1
        self._last_tick_time = replay_time

        if self._cumulative_energy_kwh >= total_energy_kwh:
            await self._stop(replay_time)
            return True

        return False

    async def _stop(self, replay_time: datetime) -> None:
        if self._completed:
            return
        if self._transaction_id is None:
            raise RuntimeError("ChargingLoop stop missing transaction id")

        meter_stop_wh = int(
            (self._cumulative_energy_kwh * Decimal("1000")).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        )
        await self.chargepoint.stop_replay_transaction(
            connector_id=self.config.connector_id,
            transaction_id=self._transaction_id,
            meter_stop=meter_stop_wh,
            timestamp=replay_time,
            id_tag=self.config.id_tag,
        )
        if self._extension_started:
            extension_sec = int((replay_time - self.plan.end_time).total_seconds())
            self.chargepoint.log(
                "REAL_CSV policy extension ended "
                f"extension_sec={extension_sec}",
                replay_timestamp=replay_time,
            )
        self._actual_stop = replay_time
        self._completed = True
        finalize_session(self._session_id())
        increment_state_version()

    def validation_snapshot(self) -> ChargingLoopValidationSnapshot:
        if not self._completed or self._actual_start is None or self._actual_stop is None:
            raise RuntimeError("ChargingLoop validation snapshot unavailable")
        expected_duration = (self.plan.end_time - self.plan.start_time).total_seconds()
        actual_duration = (self._actual_stop - self._actual_start).total_seconds()
        return {
            "session_uid": self._session_id(),
            "expected_duration_seconds": expected_duration,
            "actual_duration_seconds": actual_duration,
            "expected_total_energy_kwh": float(self.plan.total_energy_kWh),
            "actual_total_energy_kwh": float(self._cumulative_energy_kwh),
            "emitted_meter_values_count": self._ticks_emitted,
            "smart_charging_active": self._policy_limited,
        }


__all__ = ["ChargingLoop", "ChargingLoopConfig", "ChargingLoopResult"]
