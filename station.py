import asyncio
import logging
import random
from collections import deque
from datetime import datetime, timezone

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint as CP
from ocpp.v16.enums import (
    RegistrationStatus,
    ChargePointStatus,
    ChargePointErrorCode,
    ChargingProfileStatus,
    ClearChargingProfileStatus,
)
from ocpp.v16 import call, call_result

from profiles import StationProfile
from metrics import (
    record_station_started,
    record_station_stopped,
    record_transaction_started,
    record_energy_dispensed,
    record_meter_value,
)
from charging_policy import evaluate_charging_policy, evaluate_meter_value_decision

# OCPP 1.6 SmartCharging imports
from charging_profile_manager import (
    ChargingProfileManager,
    parse_charging_profile,
    ChargingRateUnit,
    ChargingProfilePurpose,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("station")


def is_peak_hour(hour: int, peak_hours: tuple = (8, 18)) -> bool:
    """Check if current hour is within peak hours (utility function for peak detection)."""
    peak_start, peak_end = peak_hours
    return peak_start <= hour < peak_end



class SimulatedChargePoint(CP):
    def __init__(self, id, connection):
        super().__init__(id, connection)
        self.id = id
        self.current_transaction_id = None
        # Initialize log buffer with max 50 entries
        self.log_buffer = deque(maxlen=50)
        
        # OCPP 1.6 SmartCharging: Initialize profile manager
        self.profile_manager = ChargingProfileManager()
        
        # Log startup
        self.log("Station initialized")
        self.log("SmartCharging profile manager initialized")

    def log(self, message: str) -> None:
        """
        Add a timestamped log entry to the buffer.
        
        Args:
            message: Description of the event/action
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        self.log_buffer.append(log_entry)

    def get_logs(self) -> list:
        """
        Return the current log buffer as a list.
        
        Returns:
            List of recent log entries
        """
        return list(self.log_buffer)

    # -------------------- OCPP HANDLERS --------------------

    @on("Reset")
    async def on_reset(self, type, **kwargs):
        logger.info(f"{self.id}: Received Reset request: type={type}")
        return {"status": "Accepted"}

    @on("RemoteStartTransaction")
    async def on_remote_start_transaction(self, id_tag, connector_id, **kwargs):
        logger.info(
            f"{self.id}: RemoteStartTransaction for tag {id_tag} on connector {connector_id}"
        )
        return {"status": "Accepted"}

    @on("RemoteStopTransaction")
    async def on_remote_stop_transaction(self, transaction_id, **kwargs):
        logger.info(f"{self.id}: RemoteStopTransaction for tx {transaction_id}")
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
                charging_rate_unit=rate_unit
            )
            
            if schedule:
                # Convert schedule to OCPP dict format
                schedule_dict = schedule.to_dict()
                schedule_start = datetime.now(timezone.utc).isoformat()
                
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
        charging_rate_unit: str = "W"
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
            
            unit = ChargingRateUnit.W if charging_rate_unit == "W" else ChargingRateUnit.A
            schedule = self.profile_manager.get_composite_schedule(
                connector_id=connector_id,
                duration=duration,
                charging_rate_unit=unit
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
        profile_id: int = None,
        connector_id: int = None,
        purpose: str = None,
        stack_level: int = None
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


# =========================================================
# MAIN STATION SIMULATOR
# =========================================================

async def simulate_station(
    station_id: str,
    csms_url: str,
    profile: StationProfile,
    current_price: float = 20.0,
    on_chargepoint_ready=None,
):
    """
    Run a single simulated charging station until cancelled.
    Implements smart charging based on price and time of day.
    
    Args:
        station_id: Unique station identifier
        csms_url: WebSocket URL for CSMS
        profile: Station behavior profile
        current_price: Current electricity price ($/kWh) - updated via reference
        on_chargepoint_ready: Optional callback to register the ChargePoint instance
    """

    # Record station startup
    record_station_started()

    ws = await websockets.connect(
        f"{csms_url}/{station_id}",
        subprotocols=["ocpp1.6"],
    )

    cp = SimulatedChargePoint(station_id, ws)
    
    # Register chargepoint instance with callback if provided
    if on_chargepoint_ready:
        on_chargepoint_ready(station_id, cp)

    # -------------------- BOOT --------------------

    async def send_boot_notification():
        cp.log("BootNotification sent")
        req = call.BootNotification(
            charge_point_model="PythonSim-Model",
            charge_point_vendor="PythonSim-Vendor",
        )
        response = await cp.call(req)
        logger.info(f"{station_id}: BootNotification response: {response}")

        status = getattr(response, "status", None)
        if status not in (RegistrationStatus.accepted, "Accepted"):
            logger.warning(f"{station_id}: Not accepted by CSMS: {status}")
            cp.log(f"BootNotification rejected: {status}")
        else:
            cp.log("BootNotification accepted")

    # -------------------- HEARTBEAT --------------------

    async def send_heartbeat_loop():
        while True:
            await asyncio.sleep(profile.heartbeat_interval)
            response = await cp.call(call.Heartbeat())
            logger.info(f"{station_id}: Heartbeat -> {response}")
            cp.log("Heartbeat sent")

    # -------------------- TRANSACTION LOOP --------------------

    async def auto_transaction_loop():
        if not profile.enable_transactions:
            logger.info(f"{station_id}: Transactions disabled by profile")
            return

        while True:
            # Idle before next session
            idle = random.randint(profile.idle_min, profile.idle_max)
            logger.info(f"{station_id}: Waiting {idle}s before new session")
            await asyncio.sleep(idle)

            id_tag = random.choice(profile.id_tags)
            connector_id = 1
            
            # Get current time and price for smart charging decisions
            current_hour = datetime.now(timezone.utc).hour
            current_price_val = current_price  # Use the passed price
            
            # ========== SMART CHARGING POLICY ENGINE ==========
            # Evaluate charging decision using policy engine
            policy_decision = evaluate_charging_policy(
                station_state={
                    "energy_dispensed": 0.0,  # Fresh session
                    "charging": False,
                    "session_active": False
                },
                profile={
                    "charge_if_price_below": profile.charge_if_price_below,
                    "max_energy_kwh": profile.max_energy_kwh,
                    "allow_peak_hours": profile.allow_peak,
                    "peak_hours": profile.peak_hours
                },
                env={
                    "current_price": current_price_val,
                    "hour": current_hour
                }
            )
            
            if policy_decision["action"] != "charge":
                logger.info(
                    f"{station_id}: Smart charging blocked - {policy_decision['reason']}"
                )
                cp.log(f"{policy_decision['reason']} — waiting")
                # Wait a bit and retry instead of progressing to next idle
                await asyncio.sleep(60)
                continue

            # Simulate flaky/offline behavior
            if random.random() < profile.offline_probability:
                logger.info(f"{station_id}: Simulating offline period")
                await ws.close()
                await asyncio.sleep(profile.offline_duration)
                logger.info(f"{station_id}: Offline period ended")
                return  # let manager restart if needed

            # -------- Authorize --------
            auth_req = call.Authorize(id_tag=id_tag)
            auth_res = await cp.call(auth_req)
            logger.info(f"{station_id}: Authorize({id_tag}) -> {auth_res}")
            auth_status = getattr(auth_res, "id_tag_info", {})
            auth_result = getattr(auth_status, "status", "Unknown") if auth_status else "Unknown"
            if auth_result == "Accepted":
                cp.log(f"Authorization successful - {id_tag}")
            else:
                cp.log(f"Authorization failed - {id_tag} ({auth_result})")

            # -------- Start Transaction --------
            start_req = call.StartTransaction(
                connector_id=connector_id,
                id_tag=id_tag,
                meter_start=0,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            start_res = await cp.call(start_req)
            logger.info(
                f"{station_id}: StartTransaction -> {start_res} "
                f"(price: ${current_price_val:.2f}, peak: {is_peak_hour(current_hour, profile.peak_hours)})"
            )
            cp.log(f"Charging started (price: ${current_price_val:.2f}, id_tag: {id_tag})")

            # Record transaction started
            record_transaction_started()

            transaction_id = getattr(start_res, "transaction_id", None)
            if transaction_id is None:
                transaction_id = random.randint(1000, 9999)
                logger.warning(
                    f"{station_id}: Missing transaction_id, using fake {transaction_id}"
                )

            self_energy = 0
            max_energy_wh = int(profile.max_energy_kwh * 1000)  # Convert kWh to Wh

            # -------- MeterValues Loop (OCPP Smart Charging + Legacy Policy) --------
            # Loop until energy limit is reached or random iterations completed
            max_iterations = random.randint(3, 8)
            for iteration in range(max_iterations):
                sample_interval_seconds = random.randint(
                    profile.sample_interval_min,
                    profile.sample_interval_max,
                )
                await asyncio.sleep(sample_interval_seconds)

                # ========== OCPP SMART CHARGING PROFILE LIMITS ==========
                # Check if OCPP charging profile limits are active
                profile_limit_w = self.profile_manager.get_current_limit(
                    connector_id=connector_id,
                    transaction_id=transaction_id
                )
                
                # Calculate base energy step using existing random range
                base_step = random.randint(
                    profile.energy_step_min,
                    profile.energy_step_max,
                )
                
                # Apply OCPP profile limits if active (takes absolute precedence)
                if profile_limit_w is not None:
                    # Convert watts to Wh based on sample interval
                    max_step_wh = profile_limit_w * (sample_interval_seconds / 3600)
                    energy_step = min(base_step, max_step_wh)
                    
                    if energy_step < base_step:
                        logger.info(
                            f"{station_id}: OCPP profile limiting charge to {profile_limit_w:.0f}W "
                            f"(step reduced from {base_step:.0f} to {energy_step:.0f} Wh)"
                        )
                        cp.log(
                            f"OCPP limit: {profile_limit_w:.0f}W → {energy_step:.0f}Wh this interval"
                        )
                    else:
                        logger.info(
                            f"{station_id}: OCPP profile allows up to {profile_limit_w:.0f}W "
                            f"(using base step {energy_step:.0f} Wh)"
                        )
                else:
                    # ========== LEGACY CHARGING POLICY (fallback when no OCPP profiles) ==========
                    logger.debug(f"{station_id}: No OCPP profiles active, using legacy policy")
                    
                    # Evaluate meter value decision using legacy policy engine
                    meter_decision = evaluate_meter_value_decision(
                        station_state={
                            "energy_dispensed": self_energy / 1000,  # Convert Wh to kWh
                            "charging": True,
                            "session_active": True
                        },
                        profile={
                            "charge_if_price_below": profile.charge_if_price_below,
                            "max_energy_kwh": profile.max_energy_kwh,
                            "allow_peak_hours": profile.allow_peak,
                            "peak_hours": profile.peak_hours
                        },
                        env={
                            "current_price": current_price_val,
                            "hour": current_hour
                        },
                        current_energy_wh=self_energy,
                        max_energy_wh=max_energy_wh
                    )
                    
                    # Check if legacy policy requires stopping
                    if meter_decision["action"] == "stop":
                        logger.info(f"{station_id}: Legacy policy stopping - {meter_decision['reason']}")
                        cp.log(f"Legacy policy: {meter_decision['reason']} — stopping")
                        break
                    
                    # Apply legacy smart charging adjustment during peak hours
                    if is_peak_hour(current_hour, profile.peak_hours) and profile.allow_peak:
                        energy_step = max(int(base_step * 0.5), 10)
                        logger.info(
                            f"{station_id}: Legacy policy peak reduction "
                            f"(step reduced from {base_step} to {energy_step} Wh)"
                        )
                    else:
                        energy_step = base_step
                
                self_energy += energy_step

                # Cap at max energy
                if self_energy >= max_energy_wh:
                    self_energy = max_energy_wh

                mv_req = call.MeterValues(
                    connector_id=connector_id,
                    transaction_id=transaction_id,
                    meter_value=[
                        {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "sampled_value": [
                                {
                                    "value": str(self_energy),
                                    "measurand": "Energy.Active.Import.Register",
                                }
                            ],
                        }
                    ],
                )

                mv_res = await cp.call(mv_req)
                logger.info(
                    f"{station_id}: MeterValues({self_energy/1000:.1f}kWh) -> {mv_res}"
                )
                
                # Record meter value update
                record_meter_value()
                
                # Exit early if energy cap reached
                if self_energy >= max_energy_wh:
                    break


            # -------- Stop Transaction --------
            stop_req = call.StopTransaction(
                transaction_id=transaction_id,
                meter_stop=self_energy,
                timestamp=datetime.now(timezone.utc).isoformat(),
                id_tag=id_tag,
            )
            stop_res = await cp.call(stop_req)
            logger.info(f"{station_id}: StopTransaction -> {stop_res}")
            
            # Record energy dispensed (convert from Wh to kWh)
            energy_kwh = self_energy / 1000.0
            cp.log(f"Charging stopped ({energy_kwh:.2f} kWh delivered)")
            record_energy_dispensed(energy_kwh)

    # -------------------- MAIN TASKS --------------------

    try:
        recv_task = asyncio.create_task(cp.start())
        hb_task = asyncio.create_task(send_heartbeat_loop())
        tx_task = asyncio.create_task(auto_transaction_loop())

        # Initial boot & status
        cp.log(f"Station startup initiated")
        await send_boot_notification()

        status_req = call.StatusNotification(
            connector_id=1,
            error_code=ChargePointErrorCode.no_error,
            status=ChargePointStatus.available,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        status_res = await cp.call(status_req)
        logger.info(f"{station_id}: StatusNotification -> {status_res}")
        cp.log("Connector available")

        await asyncio.gather(recv_task, hb_task, tx_task)

    except asyncio.CancelledError:
        logger.info(f"{station_id}: cancellation requested, shutting down.")
        cp.log("Station shutting down")
        record_station_stopped()
        try:
            await ws.close()
        except Exception:
            pass
        raise

    except Exception as e:
        logger.exception(f"{station_id}: unexpected error: {e}")
        record_station_stopped()
        try:
            await ws.close()
        except Exception:
            pass
        raise


# -------------------- MANUAL TEST --------------------

if __name__ == "__main__":
    from profiles import DEFAULT_PROFILES

    asyncio.run(
        simulate_station(
            "PYTHON-SIM-001",
            "ws://localhost:9000/ocpp",
            DEFAULT_PROFILES["default"],
        )
    )
