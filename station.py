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
)
from ocpp.v16 import call

from profiles import StationProfile
from metrics import (
    record_station_started,
    record_station_stopped,
    record_transaction_started,
    record_energy_dispensed,
    record_meter_value,
)
from charging_policy import evaluate_charging_policy, evaluate_meter_value_decision

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
        # Log startup
        self.log("Station initialized")

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

            # -------- MeterValues Loop (Smart Charging Policy Engine) --------
            # Loop until energy limit is reached or random iterations completed
            max_iterations = random.randint(3, 8)
            for iteration in range(max_iterations):
                await asyncio.sleep(
                    random.randint(
                        profile.sample_interval_min,
                        profile.sample_interval_max,
                    )
                )

                # Evaluate meter value decision using policy engine
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
                
                # Check if policy requires stopping
                if meter_decision["action"] == "stop":
                    logger.info(f"{station_id}: {meter_decision['reason']}")
                    cp.log(f"{meter_decision['reason']} — stopping")
                    # Send final meter value if needed, then break
                    break
                
                # Use smart charging to adjust energy step during peak
                if is_peak_hour(current_hour, profile.peak_hours) and profile.allow_peak:
                    base_step = random.randint(
                        profile.energy_step_min,
                        profile.energy_step_max,
                    )
                    energy_step = max(int(base_step * 0.5), 10)
                else:
                    energy_step = random.randint(
                        profile.energy_step_min,
                        profile.energy_step_max,
                    )
                
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
