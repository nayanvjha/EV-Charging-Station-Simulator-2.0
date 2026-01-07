import asyncio
import logging
from datetime import datetime, timezone

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint as CP
from ocpp.v16 import call, call_result
from ocpp.v16.enums import RegistrationStatus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("csms")


# ============================================================================
# HELPER FUNCTIONS - OCPP Smart Charging Profile Generators
# ============================================================================

def create_charge_point_max_profile(profile_id: int, max_power_w: float) -> dict:
    """
    Create a ChargePointMaxProfile limiting total station power.
    
    Args:
        profile_id: Unique profile identifier
        max_power_w: Maximum power limit in watts
        
    Returns:
        OCPP profile dict ready for SetChargingProfile
        
    Example:
        >>> profile = create_charge_point_max_profile(1, 22000)
        >>> # Limits entire charge point to 22kW
    """
    return {
        "chargingProfileId": profile_id,
        "stackLevel": 0,
        "chargingProfilePurpose": "ChargePointMaxProfile",
        "chargingProfileKind": "Absolute",
        "chargingSchedule": {
            "chargingRateUnit": "W",
            "chargingSchedulePeriod": [
                {"startPeriod": 0, "limit": max_power_w}
            ],
            "startSchedule": datetime.now(timezone.utc).isoformat()
        }
    }


def create_time_of_use_profile(
    profile_id: int,
    off_peak_w: float,
    peak_w: float,
    peak_start_hour: int,
    peak_end_hour: int
) -> dict:
    """
    Create a daily recurring TxDefaultProfile with off-peak and peak limits.
    
    Args:
        profile_id: Unique profile identifier
        off_peak_w: Power limit during off-peak hours (watts)
        peak_w: Power limit during peak hours (watts)
        peak_start_hour: Hour when peak period starts (0-23)
        peak_end_hour: Hour when peak period ends (0-23)
        
    Returns:
        OCPP profile dict with daily recurring schedule
        
    Example:
        >>> profile = create_time_of_use_profile(2, 11000, 7000, 8, 18)
        >>> # 11kW off-peak, 7kW during 8am-6pm peak hours
    """
    # Calculate seconds from midnight
    peak_start_seconds = peak_start_hour * 3600
    peak_end_seconds = peak_end_hour * 3600
    
    # Start schedule at midnight today
    start_time = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    
    return {
        "chargingProfileId": profile_id,
        "stackLevel": 0,
        "chargingProfilePurpose": "TxDefaultProfile",
        "chargingProfileKind": "Recurring",
        "recurrencyKind": "Daily",
        "chargingSchedule": {
            "chargingRateUnit": "W",
            "chargingSchedulePeriod": [
                {"startPeriod": 0, "limit": off_peak_w},  # Midnight to peak start
                {"startPeriod": peak_start_seconds, "limit": peak_w},  # Peak hours
                {"startPeriod": peak_end_seconds, "limit": off_peak_w}  # Peak end to midnight
            ],
            "startSchedule": start_time.isoformat(),
            "duration": 86400  # 24 hours
        }
    }


def create_energy_cap_profile(
    profile_id: int,
    transaction_id: int,
    max_energy_wh: float,
    duration_seconds: int,
    power_limit_w: float = 11000
) -> dict:
    """
    Create a TxProfile for a specific transaction with power limit and duration.
    
    Args:
        profile_id: Unique profile identifier
        transaction_id: Transaction this profile applies to
        max_energy_wh: Maximum energy to dispense (Wh)
        duration_seconds: Profile duration in seconds
        power_limit_w: Power limit in watts (default 11kW)
        
    Returns:
        OCPP profile dict for specific transaction
        
    Example:
        >>> profile = create_energy_cap_profile(3, 1234, 30000, 7200, 11000)
        >>> # Limit transaction 1234 to 30kWh over 2 hours at 11kW max
    """
    return {
        "chargingProfileId": profile_id,
        "transactionId": transaction_id,
        "stackLevel": 0,
        "chargingProfilePurpose": "TxProfile",
        "chargingProfileKind": "Absolute",
        "chargingSchedule": {
            "chargingRateUnit": "W",
            "chargingSchedulePeriod": [
                {"startPeriod": 0, "limit": power_limit_w}
            ],
            "startSchedule": datetime.now(timezone.utc).isoformat(),
            "duration": duration_seconds
        }
    }


class CentralSystemChargePoint(CP):
    @on("BootNotification")
    async def on_boot_notification(self, charge_point_model, charge_point_vendor, **kwargs):
        logger.info(f"{self.id}: BootNotification model={charge_point_model}, vendor={charge_point_vendor}")
        # Return a dataclass instance, NOT a dict
        return call_result.BootNotification(
            current_time=datetime.now(timezone.utc).isoformat(),
            interval=60,
            status=RegistrationStatus.accepted,   # or "Accepted" also works in most versions
        )

    @on("Heartbeat")
    async def on_heartbeat(self, **kwargs):
        logger.info(f"{self.id}: Heartbeat")
        return call_result.Heartbeat(
            current_time=datetime.now(timezone.utc).isoformat()
        )

    @on("StatusNotification")
    async def on_status_notification(self, connector_id, error_code, status, **kwargs):
        logger.info(
            f"{self.id}: StatusNotification connector={connector_id}, "
            f"status={status}, error_code={error_code}"
        )
        # Empty payload object
        return call_result.StatusNotification()

    @on("Authorize")
    async def on_authorize(self, id_tag, **kwargs):
        logger.info(f"{self.id}: Authorize id_tag={id_tag}")
        return call_result.Authorize(
            id_tag_info={"status": "Accepted"}
        )

    @on("StartTransaction")
    async def on_start_transaction(self, connector_id, id_tag, meter_start, timestamp, **kwargs):
        logger.info(
            f"{self.id}: StartTransaction id_tag={id_tag}, connector={connector_id}, meter_start={meter_start}"
        )
        return call_result.StartTransaction(
            transaction_id=1234,
            id_tag_info={"status": "Accepted"},
        )

    @on("MeterValues")
    async def on_meter_values(self, connector_id, transaction_id, meter_value, **kwargs):
        logger.info(
            f"{self.id}: MeterValues connector={connector_id}, tx={transaction_id}, values={meter_value}"
        )
        return call_result.MeterValues()

    @on("StopTransaction")
    async def on_stop_transaction(self, transaction_id, meter_stop, timestamp, id_tag=None, **kwargs):
        logger.info(
            f"{self.id}: StopTransaction tx={transaction_id}, meter_stop={meter_stop}, id_tag={id_tag}"
        )
        return call_result.StopTransaction(
            id_tag_info={"status": "Accepted"},
        )

    # ========================================================================
    # CSMS-Initiated SmartCharging Operations (Testing Helpers)
    # ========================================================================

    async def send_charging_profile_to_station(
        self,
        connector_id: int,
        profile_dict: dict
    ) -> dict:
        """
        Send a charging profile to the station via SetChargingProfile.req.
        
        Args:
            connector_id: Connector to set profile on (0 = charge point level)
            profile_dict: Complete OCPP charging profile dictionary
            
        Returns:
            Response dict with 'status' or error information
            
        Example:
            >>> profile = create_charge_point_max_profile(1, 22000)
            >>> response = await csms.send_charging_profile_to_station(0, profile)
            >>> print(response['status'])  # 'Accepted' or 'Rejected'
        """
        try:
            logger.info(
                f"{self.id}: Sending SetChargingProfile to connector {connector_id}, "
                f"profile_id={profile_dict.get('chargingProfileId')}"
            )
            
            request = call.SetChargingProfile(
                connector_id=connector_id,
                cs_charging_profiles=profile_dict
            )
            
            response = await self.call(request)
            
            logger.info(
                f"{self.id}: SetChargingProfile response: {response.status}"
            )
            
            return {
                "status": response.status,
                "connector_id": connector_id,
                "profile_id": profile_dict.get('chargingProfileId')
            }
            
        except Exception as e:
            logger.exception(
                f"{self.id}: SetChargingProfile failed: {e}"
            )
            return {
                "status": "Error",
                "error": str(e),
                "connector_id": connector_id
            }

    async def request_composite_schedule_from_station(
        self,
        connector_id: int,
        duration: int,
        charging_rate_unit: str = "W"
    ) -> dict:
        """
        Request the composite charging schedule from the station.
        
        Args:
            connector_id: Connector to get schedule for
            duration: Duration in seconds for the schedule
            charging_rate_unit: Rate unit ('W' for Watts, 'A' for Amps)
            
        Returns:
            Response dict with status, schedule, and metadata
            
        Example:
            >>> response = await csms.request_composite_schedule_from_station(1, 3600)
            >>> if response['status'] == 'Accepted':
            ...     schedule = response['chargingSchedule']
        """
        try:
            logger.info(
                f"{self.id}: Requesting GetCompositeSchedule from connector {connector_id}, "
                f"duration={duration}s, unit={charging_rate_unit}"
            )
            
            request = call.GetCompositeSchedule(
                connector_id=connector_id,
                duration=duration,
                charging_rate_unit=charging_rate_unit
            )
            
            response = await self.call(request)
            
            logger.info(
                f"{self.id}: GetCompositeSchedule response: {response.status}"
            )
            
            result = {
                "status": response.status,
                "connector_id": getattr(response, 'connector_id', connector_id)
            }
            
            # Add schedule if accepted
            if response.status == "Accepted" and hasattr(response, 'charging_schedule'):
                result["schedule_start"] = getattr(response, 'schedule_start', None)
                result["chargingSchedule"] = response.charging_schedule
                
                # Count periods for logging
                periods = response.charging_schedule.get('chargingSchedulePeriod', [])
                logger.info(
                    f"{self.id}: Received composite schedule with {len(periods)} periods"
                )
            
            return result
            
        except Exception as e:
            logger.exception(
                f"{self.id}: GetCompositeSchedule failed: {e}"
            )
            return {
                "status": "Error",
                "error": str(e),
                "connector_id": connector_id
            }

    async def clear_charging_profile_from_station(
        self,
        profile_id: int = None,
        connector_id: int = None,
        purpose: str = None,
        stack_level: int = None
    ) -> dict:
        """
        Clear charging profiles from the station with optional filters.
        
        Args:
            profile_id: Specific profile ID to clear (optional)
            connector_id: Connector to clear profiles from (optional, 0 = all)
            purpose: Profile purpose filter (optional)
            stack_level: Stack level filter (optional)
            
        Returns:
            Response dict with status and number of profiles cleared
            
        Example:
            >>> # Clear all profiles on connector 1
            >>> response = await csms.clear_charging_profile_from_station(connector_id=1)
            >>> 
            >>> # Clear specific profile
            >>> response = await csms.clear_charging_profile_from_station(profile_id=1)
            >>> 
            >>> # Clear all TxDefaultProfile profiles
            >>> response = await csms.clear_charging_profile_from_station(
            ...     purpose="TxDefaultProfile"
            ... )
        """
        try:
            # Build filter description for logging
            filters = []
            if profile_id is not None:
                filters.append(f"profile_id={profile_id}")
            if connector_id is not None:
                filters.append(f"connector_id={connector_id}")
            if purpose is not None:
                filters.append(f"purpose={purpose}")
            if stack_level is not None:
                filters.append(f"stack_level={stack_level}")
            
            filter_str = ", ".join(filters) if filters else "no filters (all profiles)"
            
            logger.info(
                f"{self.id}: Sending ClearChargingProfile with {filter_str}"
            )
            
            # Build request with optional parameters
            request_params = {}
            if profile_id is not None:
                request_params['id'] = profile_id
            if connector_id is not None:
                request_params['connector_id'] = connector_id
            if purpose is not None:
                request_params['charging_profile_purpose'] = purpose
            if stack_level is not None:
                request_params['stack_level'] = stack_level
            
            request = call.ClearChargingProfile(**request_params)
            
            response = await self.call(request)
            
            logger.info(
                f"{self.id}: ClearChargingProfile response: {response.status}"
            )
            
            return {
                "status": response.status,
                "filters": {
                    "profile_id": profile_id,
                    "connector_id": connector_id,
                    "purpose": purpose,
                    "stack_level": stack_level
                }
            }
            
        except Exception as e:
            logger.exception(
                f"{self.id}: ClearChargingProfile failed: {e}"
            )
            return {
                "status": "Error",
                "error": str(e)
            }


async def on_connect(connection):
    # websockets >= 12 gives ServerConnection object
    logger.info(f"New raw connection object: {type(connection)}")

    path = "/"
    try:
        if hasattr(connection, "path"):
            path = connection.path
        elif hasattr(connection, "request") and hasattr(connection.request, "path"):
            path = connection.request.path
    except Exception as e:
        logger.warning(f"Failed to get path from connection: {e}")

    parts = path.rstrip("/").split("/")
    station_id = parts[-1] if parts and parts[-1] else "UNKNOWN"

    logger.info(f"New connection from station: {station_id}, path={path}")

    cp = CentralSystemChargePoint(station_id, connection)
    try:
        await cp.start()
    except Exception as e:
        logger.exception(f"Error in connection handler for {station_id}: {e}")


async def main():
    server = await websockets.serve(
        on_connect,
        "0.0.0.0",
        9000,
        subprotocols=["ocpp1.6"],
    )
    logger.info("CSMS listening on ws://0.0.0.0:9000/ocpp/<station_id>")
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
