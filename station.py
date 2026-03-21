import logging
from contextlib import contextmanager
from collections import deque
from typing import Tuple, Optional, Dict, Callable

from ocpp.routing import on
from ocpp.v16 import ChargePoint as CP
from ocpp.v16.enums import (
    ChargingProfileStatus,
    ClearChargingProfileStatus,
)
from ocpp.v16 import call, call_result
from ocpp_compat import build_call

from metrics import (
    record_transaction_started,
    record_energy_dispensed,
    record_meter_value,
)
from meter_values_generator import build_meter_values, build_meter_values_with_power_soc
from live_metrics import record_live_metrics
from replay_mode import assert_real_csv_entry_active, is_real_csv_mode, is_strict_mode
from fault_injector import fault_manager, FaultType
from ev_model import BatteryModel
from db import (
    add_energy_snapshot,
    log_station_message,
    save_charging_profile,
    start_session_history,
    stop_session_history,
)

# OCPP 1.6 SmartCharging imports
from charging_profile_manager import (
    ChargingProfileManager,
    parse_charging_profile,
    ChargingRateUnit,
    ChargingProfilePurpose,
)

DOMAIN = "CHARGING"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("station")


def is_peak_hour(hour: int, peak_hours: tuple = (8, 18)) -> bool:
    """Check if current hour is within peak hours (utility function for peak detection)."""
    peak_start, peak_end = peak_hours
    return peak_start <= hour < peak_end


def get_next_session_plan(
    session_plan_provider: Optional[Callable[[str], Optional[Dict[str, object]]]],
    station_id: str,
) -> Optional[Dict[str, object]]:
    """
    Fetch the next session plan for a station.

        TODO: TO BE DRIVEN BY REAL CSV SESSION DATA
        The provider must return a dict containing CSV-driven session fields:
            - idle_seconds
            - id_tag
            - connector_id
            - meter_start_wh
            - meter_stop_wh
            - meter_intervals_sec
            - meter_values_wh
            - price_per_kwh (optional)
            - offline_seconds (optional)
            - transaction_id (optional)
    """
    if session_plan_provider is None:
        raise RuntimeError(
            "Session plan provider required; charging must be driven by replay engine"
        )
    return session_plan_provider(station_id)



class SimulatedChargePoint(CP):
    def __init__(self, id, connection):
        super().__init__(id, connection)
        self.id = id
        self.current_transaction_id = None
        self._last_transaction_id = None
        self._active_transactions_by_connector = {}
        self._active_sessions_by_connector = {}
        self._replay_context_active = False
        self._heartbeat_flood_until = None
        self._duplicate_tx_until = None
        self._tamper_until = None
        self._tamper_target = None
        self._tamper_type = None
        self._boot_times = deque(maxlen=10)
        # Initialize log buffer with max 50 entries
        self.log_buffer = deque(maxlen=50)
        
        # OCPP 1.6 SmartCharging: Initialize profile manager
        self.profile_manager = ChargingProfileManager()

        # EV battery model (per-station)
        self.battery = BatteryModel()
        
        # Log startup
        self.log("Station initialized")
        self.log("SmartCharging profile manager initialized")
        self.log(
            f"EV battery: {self.battery.capacity_kwh:.1f}kWh, "
            f"SOC {self.battery.soc_kwh:.1f}kWh, max {self.battery.max_charge_power_kw:.1f}kW"
        )

    def update_battery_profile(
        self,
        capacity_kwh=None,
        soc_kwh=None,
        temperature_c=None,
        max_charge_power_kw=None,
        tapering_enabled=None,
    ) -> None:
        self.battery.update_profile(
            capacity_kwh=capacity_kwh,
            soc_kwh=soc_kwh,
            temperature_c=temperature_c,
            max_charge_power_kw=max_charge_power_kw,
            tapering_enabled=tapering_enabled,
        )
        self.log(
            f"Battery profile updated: {self.battery.capacity_kwh:.1f}kWh, "
            f"SOC {self.battery.soc_kwh:.1f}kWh, "
            f"temp {self.battery.temperature_c:.1f}C, "
            f"max {self.battery.max_charge_power_kw:.1f}kW, "
            f"tapering {self.battery.tapering_enabled}"
        )

    async def _apply_fault(self, message_type: str) -> Tuple[bool, bool]:
        """
        Apply any active fault for this station/message.

        Returns:
            (should_drop, should_corrupt)
        """
        fault = fault_manager.check_fault(self.id, message_type)
        if not fault:
            return False, False

        if fault.fault_type == FaultType.TIMEOUT:
            delay = fault.duration or 5
            self.log(f"{message_type}: TIMEOUT {delay}s (Fault Injection)")
            return True, False

        if fault.fault_type == FaultType.DISCONNECT:
            duration = fault.duration or 5
            self.log(f"{message_type}: DISCONNECT for {duration}s (Fault Injection)")
            try:
                await self._connection.close()
            except Exception:
                pass
            return True, False

        if fault.fault_type == FaultType.DROP_MESSAGE:
            self.log(f"{message_type}: DROP_MESSAGE (Fault Injection)")
            return True, False

        if fault.fault_type == FaultType.CORRUPT_PAYLOAD:
            self.log(f"{message_type}: CORRUPT_PAYLOAD (Fault Injection)")
            return False, True

        return False, False

    def _corrupt_request_payload(self, request, corruption_type: str = "truncate_field"):
        for attr in ("id_tag", "meter_start", "meter_stop", "transaction_id"):
            if hasattr(request, attr):
                try:
                    if corruption_type == "truncate_field":
                        setattr(request, attr, "")
                    else:
                        setattr(request, attr, "CORRUPTED")
                except Exception:
                    pass
                return request
        return request

    async def call(self, payload, suppress: bool = False):
        message_type = payload.__class__.__name__
        if message_type in {"MeterValues", "StartTransaction", "StopTransaction"} and not self._replay_context_active:
            raise RuntimeError(
                "Transaction outside replay forbidden"
            )
        should_drop, should_corrupt = await self._apply_fault(message_type)
        if should_drop:
            return None
        if should_corrupt:
            payload = self._corrupt_request_payload(payload)
        return await super().call(payload, suppress=suppress)

    @contextmanager
    def _replay_context(self, action: str):
        if self._replay_context_active:
            raise RuntimeError(f"Nested replay context not allowed: {action}")
        self._replay_context_active = True
        try:
            yield
        finally:
            self._replay_context_active = False

    def _assert_replay_context(self, action: str) -> None:
        if not self._replay_context_active:
            raise RuntimeError(
                f"Charging state change outside replay context: {action}"
            )

    def _set_active_transaction(self, connector_id: int, transaction_id: int, session_id: str) -> None:
        self._assert_replay_context("set_active_transaction")
        self._active_transactions_by_connector[connector_id] = transaction_id
        self._active_sessions_by_connector[connector_id] = session_id
        self.current_transaction_id = transaction_id

    def _clear_active_transaction(self, connector_id: int, transaction_id: int) -> None:
        self._assert_replay_context("clear_active_transaction")
        self._active_transactions_by_connector.pop(connector_id, None)
        self._active_sessions_by_connector.pop(connector_id, None)
        if self.current_transaction_id == transaction_id:
            self.current_transaction_id = None

    def _is_heartbeat_flood_active(self) -> bool:
        return False

    def _is_duplicate_tx_active(self) -> bool:
        return False

    def _is_tamper_active(self) -> bool:
        return False

    def enable_heartbeat_flood(self, duration: float = 30) -> None:
        raise RuntimeError("Wall-clock security faults are forbidden; use replay-driven faults")

    def enable_duplicate_transactions(self, duration: float = 30) -> None:
        raise RuntimeError("Wall-clock security faults are forbidden; use replay-driven faults")

    def enable_tamper_payload(
        self,
        target_message: str = None,
        corruption_type: str = "truncate_field",
        duration: float = 30,
    ) -> None:
        raise RuntimeError("Wall-clock tamper faults are forbidden; use replay-driven faults")

    def log(self, message: str, replay_timestamp: object = None) -> None:
        """
        Add a timestamped log entry to the buffer.
        
        Args:
            message: Description of the event/action
        """
        if replay_timestamp is None:
            log_entry = message
            self.log_buffer.append(log_entry)
            return
        timestamp_text = str(replay_timestamp)
        log_entry = f"[{timestamp_text}] {message}"
        self.log_buffer.append(log_entry)
        log_station_message(self.id, message, timestamp=replay_timestamp)

    def get_logs(self) -> list:
        """
        Return the current log buffer as a list.
        
        Returns:
            List of recent log entries
        """
        return list(self.log_buffer)

    # -------------------- REPLAY API --------------------

    async def start_replay_transaction(
        self,
        connector_id: int,
        id_tag: str,
        meter_start: int,
        timestamp: object,
        session_id: str,
    ) -> int:
        """
        Start a deterministic replay transaction on a connector.
        """
        with self._replay_context("start_replay_transaction"):
            self._assert_replay_context("start_replay_transaction")
            if connector_id in self._active_transactions_by_connector:
                raise ValueError(
                    f"Connector {connector_id} already has an active transaction"
                )

            if not hasattr(timestamp, "isoformat"):
                raise RuntimeError("Replay timestamp required")
            req = build_call(
                "StartTransaction",
                connector_id=connector_id,
                id_tag=id_tag,
                meter_start=meter_start,
                timestamp=timestamp.isoformat(),
            )
            res = await self.call(req)

            transaction_id = getattr(res, "transaction_id", None)
            if transaction_id is None:
                raise ValueError("StartTransaction response missing transaction_id")

            self._set_active_transaction(connector_id, transaction_id, session_id)

            record_transaction_started()
            start_session_history(
                session_id=transaction_id,
                station_id=self.id,
                start_time=timestamp,
            )
            self.log(
                f"Replay StartTransaction (tx={transaction_id}, connector={connector_id}, session={session_id})",
                replay_timestamp=timestamp,
            )
            return transaction_id

    async def emit_replay_meter_values(
        self,
        connector_id: int,
        transaction_id: int,
        energy_wh: float,
        timestamp: object,
    ) -> None:
        """
        Emit deterministic MeterValues for an active replay transaction.
        """
        with self._replay_context("emit_replay_meter_values"):
            self._assert_replay_context("emit_replay_meter_values")
            active_tx = self._active_transactions_by_connector.get(connector_id)
            if active_tx != transaction_id:
                raise ValueError(
                    f"MeterValues tx mismatch for connector {connector_id}: "
                    f"active={active_tx}, got={transaction_id}"
                )

            if not hasattr(timestamp, "isoformat"):
                raise RuntimeError("Replay timestamp required")
            mv_req = build_meter_values(
                connector_id=connector_id,
                transaction_id=transaction_id,
                energy_wh=energy_wh,
                timestamp=timestamp,
            )

            await self.call(mv_req)

            record_meter_value()
            add_energy_snapshot(
                station_id=self.id,
                energy_kwh=energy_wh / 1000.0,
                timestamp=timestamp,
            )

    async def emit_replay_meter_values_live(
        self,
        connector_id: int,
        transaction_id: int,
        energy_wh: float,
        power_kw: float,
        soc_percent: float,
        timestamp: object,
    ) -> None:
        """
        Emit live MeterValues for REAL_CSV charging loop with power and SOC.
        """
        if is_strict_mode():
            raise RuntimeError("Live MeterValues are forbidden in STRICT mode")
        if not is_real_csv_mode():
            raise RuntimeError("Live MeterValues require REAL_CSV mode")
        assert_real_csv_entry_active()
        with self._replay_context("emit_replay_meter_values_live"):
            self._assert_replay_context("emit_replay_meter_values_live")
            active_tx = self._active_transactions_by_connector.get(connector_id)
            if active_tx != transaction_id:
                raise ValueError(
                    f"MeterValues tx mismatch for connector {connector_id}: "
                    f"active={active_tx}, got={transaction_id}"
                )

            if not hasattr(timestamp, "isoformat"):
                raise RuntimeError("Replay timestamp required")
            mv_req = build_meter_values_with_power_soc(
                connector_id=connector_id,
                transaction_id=transaction_id,
                energy_wh=energy_wh,
                power_kw=power_kw,
                soc_percent=soc_percent,
                timestamp=timestamp,
            )

            await self.call(mv_req)

            record_meter_value()
            add_energy_snapshot(
                station_id=self.id,
                energy_kwh=energy_wh / 1000.0,
                timestamp=timestamp,
            )
            record_live_metrics(
                station_id=self.id,
                power_kw=power_kw,
                energy_kwh=energy_wh / 1000.0,
                soc_percent=soc_percent,
                timestamp=timestamp,
            )

    async def stop_replay_transaction(
        self,
        connector_id: int,
        transaction_id: int,
        meter_stop: int,
        timestamp: object,
        id_tag: str,
    ) -> None:
        """
        Stop a deterministic replay transaction on a connector.
        """
        with self._replay_context("stop_replay_transaction"):
            self._assert_replay_context("stop_replay_transaction")
            active_tx = self._active_transactions_by_connector.get(connector_id)
            if active_tx != transaction_id:
                raise ValueError(
                    f"StopTransaction tx mismatch for connector {connector_id}: "
                    f"active={active_tx}, got={transaction_id}"
                )

            if not hasattr(timestamp, "isoformat"):
                raise RuntimeError("Replay timestamp required")
            stop_req = build_call(
                "StopTransaction",
                transaction_id=transaction_id,
                meter_stop=meter_stop,
                timestamp=timestamp.isoformat(),
                id_tag=id_tag,
            )
            await self.call(stop_req)

            energy_kwh = meter_stop / 1000.0
            record_energy_dispensed(energy_kwh)
            stop_session_history(
                session_id=transaction_id,
                station_id=self.id,
                stop_time=timestamp,
                energy_kwh=energy_kwh,
            )

            self._clear_active_transaction(connector_id, transaction_id)

            self.log(
                f"Replay StopTransaction (tx={transaction_id}, connector={connector_id})",
                replay_timestamp=timestamp,
            )

    # -------------------- OCPP HANDLERS --------------------

    @on("Reset")
    async def on_reset(self, type, **kwargs):
        should_drop, _ = await self._apply_fault("Reset")
        if should_drop:
            return None
        logger.info(f"{self.id}: Received Reset request: type={type}")
        return {"status": "Accepted"}

    @on("RemoteStartTransaction")
    async def on_remote_start_transaction(self, id_tag, connector_id, **kwargs):
        should_drop, _ = await self._apply_fault("RemoteStartTransaction")
        if should_drop:
            return None
        logger.info(
            f"{self.id}: RemoteStartTransaction for tag {id_tag} on connector {connector_id}"
        )
        return {"status": "Accepted"}

    @on("RemoteStopTransaction")
    async def on_remote_stop_transaction(self, transaction_id, **kwargs):
        should_drop, _ = await self._apply_fault("RemoteStopTransaction")
        if should_drop:
            return None
        logger.info(f"{self.id}: RemoteStopTransaction for tx {transaction_id}")
        if self.current_transaction_id is not None and transaction_id != self.current_transaction_id:
            logger.warning(
                "Security event (duplicate transaction) station=%s tx=%s current=%s",
                self.id,
                transaction_id,
                self.current_transaction_id,
            )
        return {"status": "Accepted"}

    # -------------------- OCPP 1.6 SMARTCHARGING HANDLERS --------------------

    @on("SetChargingProfile")
    async def on_set_charging_profile(self, connector_id: int, cs_charging_profiles: dict, **kwargs):
        """
        Handle SetChargingProfile.req from CSMS.
        
        Parses the charging profile, validates it, and stores it in the profile manager.
        Returns Accepted or Rejected status based on validation and storage result.
        
        Args:
            connector_id: Connector to set profile on (0 = charge point level)
            cs_charging_profiles: Charging profile dictionary from OCPP message
            **kwargs: Additional OCPP parameters
            
        Returns:
            SetChargingProfile.conf with status
        """
        try:
            should_drop, _ = await self._apply_fault("SetChargingProfile")
            if should_drop:
                return None
            # Persist raw profile JSON on receipt
            profile_id = cs_charging_profiles.get("chargingProfileId")
            profile_schedule = cs_charging_profiles.get("chargingSchedule") or {}
            created_at = profile_schedule.get("startSchedule")
            save_charging_profile(
                self.id,
                cs_charging_profiles,
                profile_id=profile_id,
                created_at=created_at,
            )

            # Parse the charging profile from OCPP dict
            profile = parse_charging_profile(cs_charging_profiles)
            
            # Add profile to manager
            success, message = self.profile_manager.add_profile(connector_id, profile)
            
            if success:
                logger.info(
                    f"{self.id}: SetChargingProfile accepted - profile {profile.charging_profile_id} "
                    f"on connector {connector_id}"
                )
                self.log(
                    f"SetChargingProfile accepted: profile {profile.charging_profile_id} "
                    f"(purpose={profile.charging_profile_purpose.value}, "
                    f"stackLevel={profile.stack_level})"
                )
                return call_result.SetChargingProfile(
                    status=ChargingProfileStatus.accepted
                )
            else:
                logger.warning(
                    f"{self.id}: SetChargingProfile rejected - {message}"
                )
                self.log(f"SetChargingProfile rejected: {message}")
                return call_result.SetChargingProfile(
                    status=ChargingProfileStatus.rejected
                )
                
        except Exception as e:
            logger.exception(f"{self.id}: SetChargingProfile error: {e}")
            self.log(f"SetChargingProfile error: {str(e)}")
            return call_result.SetChargingProfile(
                status=ChargingProfileStatus.rejected
            )

    @on("GetCompositeSchedule")
    async def on_get_composite_schedule(self, connector_id: int, duration: int, **kwargs):
        """
        Handle GetCompositeSchedule.req from CSMS.
        
        Calculates the composite schedule by merging all applicable profiles
        for the requested connector and duration.
        
        Args:
            connector_id: Connector to get schedule for
            duration: Duration in seconds for the schedule
            **kwargs: Additional OCPP parameters including optional chargingRateUnit
            
        Returns:
            GetCompositeSchedule.conf with schedule or Rejected status
        """
        try:
            should_drop, _ = await self._apply_fault("GetCompositeSchedule")
            if should_drop:
                return None
            start_time_value = kwargs.get("start_time")
            if start_time_value is None or not hasattr(start_time_value, "isoformat"):
                raise RuntimeError("Explicit start_time required")
            start_time = start_time_value

            # Extract optional chargingRateUnit, default to "W"
            rate_unit_str = kwargs.get("charging_rate_unit", "W")
            try:
                rate_unit = ChargingRateUnit(rate_unit_str)
            except ValueError:
                rate_unit = ChargingRateUnit.WATTS
            
            # Get composite schedule
            schedule = self.profile_manager.get_composite_schedule(
                connector_id=connector_id,
                duration=duration,
                charging_rate_unit=rate_unit,
                start_time=start_time,
            )
            
            if schedule:
                # Convert schedule to OCPP dict format
                schedule_dict = schedule.to_dict()
                schedule_start = start_time.isoformat()
                
                logger.info(
                    f"{self.id}: GetCompositeSchedule accepted - "
                    f"{len(schedule.charging_schedule_period)} periods for connector {connector_id}"
                )
                self.log(
                    f"GetCompositeSchedule: {len(schedule.charging_schedule_period)} periods "
                    f"for {duration}s on connector {connector_id}"
                )
                
                return call_result.GetCompositeSchedule(
                    status="Accepted",
                    connector_id=connector_id,
                    schedule_start=schedule_start,
                    charging_schedule=schedule_dict
                )
            else:
                logger.info(
                    f"{self.id}: GetCompositeSchedule rejected - no applicable profiles"
                )
                self.log(f"GetCompositeSchedule rejected: no profiles for connector {connector_id}")
                return call_result.GetCompositeSchedule(status="Rejected")
                
        except Exception as e:
            logger.exception(f"{self.id}: GetCompositeSchedule error: {e}")
            self.log(f"GetCompositeSchedule error: {str(e)}")
            return call_result.GetCompositeSchedule(status="Rejected")

    @on("ClearChargingProfile")
    async def on_clear_charging_profile(self, **kwargs):
        """
        Handle ClearChargingProfile.req from CSMS.
        
        Removes charging profiles matching the provided criteria.
        Uses AND logic for all filters.
        
        Args:
            **kwargs: Optional filters:
                - id: Specific profile ID to remove
                - connector_id: Connector to clear profiles from (0 = all)
                - charging_profile_purpose: Purpose filter
                - stack_level: Stack level filter
                
        Returns:
            ClearChargingProfile.conf with Accepted or Unknown status
        """
        try:
            should_drop, _ = await self._apply_fault("ClearChargingProfile")
            if should_drop:
                return None
            # Extract optional filters from kwargs
            profile_id = kwargs.get("id")
            connector_id = kwargs.get("connector_id", 0)
            purpose_str = kwargs.get("charging_profile_purpose")
            stack_level = kwargs.get("stack_level")
            
            # Convert purpose string to enum if provided
            purpose = None
            if purpose_str:
                try:
                    purpose = ChargingProfilePurpose(purpose_str)
                except ValueError:
                    logger.warning(f"{self.id}: Invalid charging_profile_purpose: {purpose_str}")
            
            total_cleared = 0
            
            # If connector_id is 0, clear from all connectors
            if connector_id == 0:
                for conn_id in self.profile_manager.get_all_connector_ids():
                    cleared = self.profile_manager.clear_profile(
                        connector_id=conn_id,
                        profile_id=profile_id,
                        purpose=purpose,
                        stack_level=stack_level
                    )
                    total_cleared += cleared
            else:
                total_cleared = self.profile_manager.clear_profile(
                    connector_id=connector_id,
                    profile_id=profile_id,
                    purpose=purpose,
                    stack_level=stack_level
                )
            
            logger.info(f"{self.id}: ClearChargingProfile - cleared {total_cleared} profiles")
            self.log(f"ClearChargingProfile: cleared {total_cleared} profiles")
            
            if total_cleared > 0:
                return call_result.ClearChargingProfile(
                    status=ClearChargingProfileStatus.accepted
                )
            else:
                return call_result.ClearChargingProfile(
                    status=ClearChargingProfileStatus.unknown
                )
                
        except Exception as e:
            logger.exception(f"{self.id}: ClearChargingProfile error: {e}")
            self.log(f"ClearChargingProfile error: {str(e)}")
            return call_result.ClearChargingProfile(
                status=ClearChargingProfileStatus.unknown
            )

    # ========================================================================
    # SmartCharging API Methods (Direct profile management without CSMS)
    # ========================================================================

    async def send_charging_profile_to_station(
        self,
        connector_id: int,
        profile_dict: dict
    ) -> dict:
        """
        Add a charging profile directly to the station's profile manager.
        
        This method provides a direct interface for setting profiles without
        going through OCPP message handlers, useful for REST API integration.
        
        Args:
            connector_id: Connector to set profile on (0 = charge point level)
            profile_dict: Complete OCPP charging profile dictionary
            
        Returns:
            Response dict with 'status' or error information
        """
        try:
            # Parse the charging profile from dict
            profile = parse_charging_profile(profile_dict)
            
            # Add profile to manager
            success, message = self.profile_manager.add_profile(connector_id, profile)
            
            if success:
                logger.info(
                    f"{self.id}: Profile {profile.charging_profile_id} added to connector {connector_id}"
                )
                self.log(
                    f"Profile {profile.charging_profile_id} accepted "
                    f"(purpose={profile.charging_profile_purpose.value}, "
                    f"stackLevel={profile.stack_level})"
                )
                profile_schedule = profile_dict.get("chargingSchedule") or {}
                created_at = profile_schedule.get("startSchedule")
                save_charging_profile(
                    self.id,
                    profile_dict,
                    profile_id=profile.charging_profile_id,
                    created_at=created_at,
                )
                return {
                    "status": "Accepted",
                    "connector_id": connector_id,
                    "profile_id": profile.charging_profile_id
                }
            else:
                logger.warning(f"{self.id}: Profile rejected - {message}")
                self.log(f"Profile rejected: {message}")
                return {
                    "status": "Rejected",
                    "error": message
                }
                
        except Exception as e:
            logger.exception(f"{self.id}: send_charging_profile_to_station error: {e}")
            self.log(f"Profile error: {str(e)}")
            return {
                "status": "Error",
                "error": str(e)
            }

    async def request_composite_schedule_from_station(
        self,
        connector_id: int,
        duration: int,
        charging_rate_unit: str = "W",
        start_time: object = None,
    ) -> dict:
        """
        Get the composite schedule for a connector.
        
        Args:
            connector_id: Connector to get schedule for
            duration: Duration in seconds
            charging_rate_unit: "W" for watts or "A" for amps
            
        Returns:
            Response dict with schedule or error information
        """
        try:
            from charging_profile_manager import ChargingRateUnit
            
            if start_time is None or not hasattr(start_time, "isoformat"):
                raise RuntimeError("Explicit start_time required")

            unit = ChargingRateUnit.W if charging_rate_unit == "W" else ChargingRateUnit.A
            schedule = self.profile_manager.get_composite_schedule(
                connector_id=connector_id,
                duration=duration,
                charging_rate_unit=unit,
                start_time=start_time,
            )
            
            if schedule:
                schedule_dict = {
                    "chargingRateUnit": schedule.chargingRateUnit.value,
                    "chargingSchedulePeriod": [
                        {
                            "startPeriod": p.startPeriod,
                            "limit": p.limit,
                            "numberPhases": p.numberPhases
                        }
                        for p in schedule.chargingSchedulePeriod
                    ]
                }
                if schedule.duration:
                    schedule_dict["duration"] = schedule.duration
                if schedule.startSchedule:
                    schedule_dict["startSchedule"] = schedule.startSchedule.isoformat()
                if schedule.minChargingRate:
                    schedule_dict["minChargingRate"] = schedule.minChargingRate
                
                return {
                    "status": "Accepted",
                    "connector_id": connector_id,
                    "schedule": schedule_dict
                }
            else:
                return {
                    "status": "Rejected",
                    "connector_id": connector_id,
                    "error": "No applicable profiles"
                }
                
        except Exception as e:
            logger.exception(f"{self.id}: request_composite_schedule_from_station error: {e}")
            return {
                "status": "Error",
                "error": str(e)
            }

    async def clear_charging_profile_from_station(
        self,
        profile_id: Optional[int] = None,
        connector_id: Optional[int] = None,
        purpose: Optional[str] = None,
        stack_level: Optional[int] = None
    ) -> dict:
        """
        Clear charging profiles from the station.
        
        Args:
            profile_id: Specific profile ID to remove
            connector_id: Connector to clear profiles from (None = all)
            purpose: Purpose filter ("ChargePointMaxProfile", "TxDefaultProfile", "TxProfile")
            stack_level: Stack level filter
            
        Returns:
            Response dict with status
        """
        try:
            # Convert purpose string to enum if provided
            purpose_enum = None
            if purpose:
                try:
                    purpose_enum = ChargingProfilePurpose(purpose)
                except ValueError:
                    logger.warning(f"{self.id}: Invalid purpose: {purpose}")
            
            total_cleared = 0
            
            # If no connector specified, clear from all connectors
            if connector_id is None:
                for conn_id in self.profile_manager.get_all_connector_ids():
                    cleared = self.profile_manager.clear_profile(
                        connector_id=conn_id,
                        profile_id=profile_id,
                        purpose=purpose_enum,
                        stack_level=stack_level
                    )
                    total_cleared += cleared
                # Also clear connector 0 (charge point level)
                cleared = self.profile_manager.clear_profile(
                    connector_id=0,
                    profile_id=profile_id,
                    purpose=purpose_enum,
                    stack_level=stack_level
                )
                total_cleared += cleared
            else:
                total_cleared = self.profile_manager.clear_profile(
                    connector_id=connector_id,
                    profile_id=profile_id,
                    purpose=purpose_enum,
                    stack_level=stack_level
                )
            
            logger.info(f"{self.id}: Cleared {total_cleared} profiles")
            self.log(f"Cleared {total_cleared} charging profiles")
            
            return {
                "status": "Accepted" if total_cleared > 0 else "Unknown",
                "cleared_count": total_cleared
            }
                
        except Exception as e:
            logger.exception(f"{self.id}: clear_charging_profile_from_station error: {e}")
            return {
                "status": "Error",
                "error": str(e)
            }


