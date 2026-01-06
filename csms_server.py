import asyncio
import logging
from datetime import datetime, timezone

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint as CP
from ocpp.v16 import call_result
from ocpp.v16.enums import RegistrationStatus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("csms")


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
