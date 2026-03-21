from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

import websockets
from ocpp.v16 import call
from ocpp_compat import build_call
from ocpp.v16.enums import ChargePointErrorCode, ChargePointStatus, RegistrationStatus

from profiles import StationProfile
from metrics import record_station_started, record_station_stopped
from fault_injection import get_active_fault_scenario
from station import SimulatedChargePoint

DOMAIN = "PROTOCOL"
if DOMAIN == "CHARGING":
    raise RuntimeError("Protocol module cannot run in CHARGING domain")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("protocol_station")


async def simulate_station(
    station_id: str,
    csms_url: str,
    profile: StationProfile,
    current_price: float = 20.0,
    on_chargepoint_ready: Optional[Callable[[str, SimulatedChargePoint], None]] = None,
    session_plan_provider: Optional[Callable[[str], Optional[Dict[str, object]]]] = None,
) -> None:
    """
    Run a single simulated charging station until cancelled (protocol-only loop).
    Charging progression must be driven by replay, not timers.
    """
    record_station_started()

    ws = await websockets.connect(
        f"{csms_url}/{station_id}",
        subprotocols=["ocpp1.6"],
    )

    cp = SimulatedChargePoint(station_id, ws)

    if on_chargepoint_ready:
        on_chargepoint_ready(station_id, cp)

    async def send_boot_notification() -> None:
        cp.log("BootNotification sent")
        req = build_call(
            "BootNotification",
            charge_point_model="PythonSim-Model",
            charge_point_vendor="PythonSim-Vendor",
        )
        response = await cp.call(req)
        logger.info("%s: BootNotification response: %s", station_id, response)

        status = getattr(response, "status", None)
        if status not in (RegistrationStatus.accepted, "Accepted"):
            logger.warning("%s: Not accepted by CSMS: %s", station_id, status)
            cp.log(f"BootNotification rejected: {status}")
        else:
            cp.log("BootNotification accepted")

    async def send_heartbeat_loop() -> None:
        while True:
            interval = profile.heartbeat_interval
            await asyncio.sleep(interval)
            scenario = get_active_fault_scenario()
            if scenario is not None and scenario.should_suppress_heartbeat():
                continue
            response = await cp.call(build_call("Heartbeat"))
            logger.info("%s: Heartbeat -> %s", station_id, response)
            cp.log("Heartbeat sent")

    async def auto_transaction_loop() -> None:
        if profile.enable_transactions:
            logger.info(
                "%s: Auto transactions disabled; replay engine must drive charging",
                station_id,
            )
        return

    try:
        recv_task = asyncio.create_task(cp.start())
        hb_task = asyncio.create_task(send_heartbeat_loop())
        tx_task = asyncio.create_task(auto_transaction_loop())

        cp.log("Station startup initiated")
        await send_boot_notification()

        status_req = build_call(
            "StatusNotification",
            connector_id=1,
            error_code=ChargePointErrorCode.no_error,
            status=ChargePointStatus.available,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        status_res = await cp.call(status_req)
        logger.info("%s: StatusNotification -> %s", station_id, status_res)
        cp.log("Connector available")

        await asyncio.gather(recv_task, hb_task, tx_task)

    except asyncio.CancelledError:
        logger.info("%s: cancellation requested, shutting down.", station_id)
        cp.log("Station shutting down")
        record_station_stopped()
        try:
            await ws.close()
        except Exception:
            pass
        raise

    except Exception as exc:
        logger.exception("%s: unexpected error: %s", station_id, exc)
        record_station_stopped()
        try:
            await ws.close()
        except Exception:
            pass
        raise


if __name__ == "__main__":
    raise RuntimeError("CSV-driven profile required; defaults are forbidden")