import asyncio
import contextlib
import os
import random
import secrets
import time
from contextlib import suppress
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, Query, Depends, Security
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from prometheus_client import start_http_server
import logging

from profiles import DEFAULT_PROFILES, StationProfile
from station import simulate_station
from metrics import (
    get_metrics_text,
    set_stations_total,
    set_stations_active,
)
from csms_server import (
    create_charge_point_max_profile,
    create_time_of_use_profile,
    create_energy_cap_profile,
)
import websockets
from ocpp.v16 import ChargePoint as CP
from ocpp.v16 import call
from db import (
    init_db,
    get_station_history as db_get_station_history,
    list_sessions,
    create_user,
    get_user_by_api_key,
    get_user_by_email,
)
from security_monitor import event_to_dict, security_monitor
from security_monitor import EventType
from fault_injector import FaultRule, FaultType, fault_manager
from security_detection import flow_tracker, rule_evaluator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("controller_api")

CSMS_URL = "ws://localhost:9000/ocpp"  # keep it, but don't let it block

# ================== GLOBAL STATE ==================

CURRENT_PRICE_PER_KWH = 20.0
TOTAL_ENERGY_KWH = 0.0
TOTAL_EARNINGS = 0.0

# ================== MODELS ==================

class StationInfo(BaseModel):
    station_id: str
    profile: str
    running: bool
    usage_kw: float
    energy_kwh: float
    # Smart charging info
    max_energy_kwh: float
    charge_if_price_below: float
    allow_peak: bool
    energy_percent: float  # Percentage of max energy cap


class ScaleRequest(BaseModel):
    count: int
    profile: str = "default"


class StartRequest(BaseModel):
    station_id: str
    profile: str = "default"


class StopRequest(BaseModel):
    station_id: str


class PriceUpdate(BaseModel):
    price: float


class BatteryProfileRequest(BaseModel):
    capacity_kwh: Optional[float] = None
    soc_kwh: Optional[float] = None
    temperature_c: Optional[float] = None
    max_charge_power_kw: Optional[float] = None
    tapering_enabled: Optional[bool] = None


class UserCreateRequest(BaseModel):
    email: str


class SecurityAttackRequest(BaseModel):
    station_id: str
    action: str = Field(..., description="inject_fault | spoof_command | tamper_payload")
    type: Optional[str] = None
    duration: Optional[float] = None
    message_type: Optional[str] = None
    target_message: Optional[str] = None
    corruption_type: Optional[str] = None
    payload: Optional[dict] = None
    allow_unowned: bool = False


# ================== SMARTCHARGING MODELS ==================

class ChargingProfileRequest(BaseModel):
    """Request to send a charging profile to a station."""
    connector_id: int = Field(..., description="Connector ID (1-N, or 0 for station-wide)")
    profile: dict = Field(..., description="OCPP charging profile dict")


class CompositeScheduleRequest(BaseModel):
    """Request to get composite schedule from a station."""
    connector_id: int = Field(..., description="Connector ID")
    duration: int = Field(..., description="Duration in seconds")
    charging_rate_unit: str = Field(default="W", description="W or A")


class ClearProfileRequest(BaseModel):
    """Request to clear charging profiles from a station."""
    profile_id: Optional[int] = Field(None, description="Specific profile ID to clear")
    connector_id: Optional[int] = Field(None, description="Clear profiles for this connector")
    purpose: Optional[str] = Field(None, description="Profile purpose to clear")
    stack_level: Optional[int] = Field(None, description="Stack level to clear")


class ChargingProfileResponse(BaseModel):
    """Response from sending a charging profile."""
    status: str
    station_id: str
    connector_id: int
    profile_id: Optional[int] = None
    message: str
    error: Optional[str] = None


class CompositeScheduleResponse(BaseModel):
    """Response from composite schedule request."""
    status: str
    station_id: str
    connector_id: int
    schedule: Optional[dict] = None
    message: str
    error: Optional[str] = None


class TestProfileRequest(BaseModel):
    """Request to generate and send a test profile."""
    scenario: str = Field(..., description="Scenario: peak_shaving, time_of_use, energy_cap")
    connector_id: int = Field(default=1, description="Connector ID")
    # Scenario-specific parameters
    max_power_w: Optional[float] = Field(None, description="For peak_shaving: max power limit")
    off_peak_w: Optional[float] = Field(None, description="For time_of_use: off-peak power")
    peak_w: Optional[float] = Field(None, description="For time_of_use: peak power")
    peak_start_hour: Optional[int] = Field(None, description="For time_of_use: peak start hour")
    peak_end_hour: Optional[int] = Field(None, description="For time_of_use: peak end hour")
    transaction_id: Optional[int] = Field(None, description="For energy_cap: transaction ID")
    max_energy_wh: Optional[float] = Field(None, description="For energy_cap: max energy")
    duration_seconds: Optional[int] = Field(None, description="For energy_cap: duration")
    power_limit_w: Optional[float] = Field(None, description="For energy_cap: power limit")


class TestProfileResponse(BaseModel):
    """Response from test profile generation."""
    status: str
    station_id: str
    scenario: str
    profile: dict
    send_status: str
    message: str
    error: Optional[str] = None


# ================== MANAGER ==================

class StationManager:
    def __init__(self, csms_url: str, profiles: Dict[str, StationProfile]):
        self.csms_url = csms_url
        self.profiles = profiles
        self.tasks: Dict[str, asyncio.Task] = {}
        self.station_profiles: Dict[str, str] = {}
        self.station_usage: Dict[str, float] = {}
        self.station_energy_kwh: Dict[str, float] = {}
        self.station_chargepoints: Dict[str, object] = {}  # Store ChargePoint instances for log access
        self.station_owners: Dict[str, int] = {}

    def list_stations(self, user_id: int) -> List[StationInfo]:
        result = []
        for sid, task in self.tasks.items():
            if self.station_owners.get(sid) != user_id:
                continue
            profile_name = self.station_profiles.get(sid, "default")
            profile = self.profiles.get(profile_name)
            energy = self.station_energy_kwh.get(sid, 0.0)
            max_energy = profile.max_energy_kwh if profile else 30.0
            energy_pct = (energy / max_energy * 100) if max_energy > 0 else 0
            
            result.append(
                StationInfo(
                    station_id=sid,
                    profile=profile_name,
                    running=not task.done(),
                    usage_kw=round(self.station_usage.get(sid, 0.0), 2),
                    energy_kwh=round(energy, 3),
                    max_energy_kwh=profile.max_energy_kwh if profile else 30.0,
                    charge_if_price_below=profile.charge_if_price_below if profile else 100.0,
                    allow_peak=profile.allow_peak if profile else True,
                    energy_percent=round(energy_pct, 1),
                )
            )
        return result

    async def start_station(self, user_id: int, station_id: str, profile_name: str):
        if station_id in self.tasks and not self.tasks[station_id].done():
            if self.station_owners.get(station_id) != user_id:
                raise ValueError("Station ID already owned by another user")
            return

        profile = self.profiles.get(profile_name)
        if not profile:
            raise ValueError("Unknown profile")

        def on_chargepoint_ready(sid, chargepoint):
            """Callback to register chargepoint instance."""
            self.station_chargepoints[sid] = chargepoint

        async def safe_sim():
            try:
                # 🔥 THIS PREVENTS FREEZE IF CSMS IS DOWN
                await asyncio.wait_for(
                    simulate_station(
                        station_id,
                        self.csms_url,
                        profile,
                        current_price=CURRENT_PRICE_PER_KWH,
                        on_chargepoint_ready=on_chargepoint_ready,
                    ),
                    timeout=2.0
                )
            except asyncio.TimeoutError:
                # fallback to dummy loop instead of blocking
                while True:
                    await asyncio.sleep(1)

        task = asyncio.create_task(safe_sim())

        self.tasks[station_id] = task
        self.station_profiles[station_id] = profile_name
        self.station_usage[station_id] = 0.0
        self.station_energy_kwh[station_id] = 0.0
        self.station_owners[station_id] = user_id

    async def stop_station(self, user_id: int, station_id: str):
        task = self.tasks.get(station_id)
        if not task:
            return
        if self.station_owners.get(station_id) != user_id:
            raise ValueError("Station not owned by user")

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

        self.station_usage[station_id] = 0.0

    async def scale_to(self, user_id: int, target_count: int, profile_name: str = "default"):
        current_ids = sorted(
            sid for sid, owner in self.station_owners.items() if owner == user_id
        )
        current_count = len(current_ids)

        # Stop all existing stations first
        for sid in current_ids:
            await self.stop_station(user_id, sid)
        
        # Create new stations with the specified profile
        for i in range(1, target_count + 1):
            sid = f"PY-SIM-{i:04d}"
            await self.start_station(user_id, sid, profile_name)

    def get_station_logs(self, user_id: int, station_id: str) -> List[str]:
        """
        Get recent log entries for a specific station.
        
        Args:
            station_id: The station identifier
            
        Returns:
            List of recent log entries, empty list if station not found
        """
        if self.station_owners.get(station_id) != user_id:
            return []
        chargepoint = self.station_chargepoints.get(station_id)
        if not chargepoint:
            return []
        return chargepoint.get_logs()

    def get_user_station_ids(self, user_id: int) -> List[str]:
        return [sid for sid, owner in self.station_owners.items() if owner == user_id]

# ================== APP ==================

app = FastAPI(title="EV Station Simulator Controller")

app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")
manager = StationManager(CSMS_URL, DEFAULT_PROFILES)


async def _send_spoofed_command(
    station_id: str,
    message_type: str,
    payload: dict,
) -> None:
    ws = await websockets.connect(
        f"{CSMS_URL}/{station_id}",
        subprotocols=["ocpp1.6"],
    )
    cp = CP(station_id, ws)
    recv_task = asyncio.create_task(cp.start())
    try:
        if message_type == "BootNotification":
            req = call.BootNotification(
                charge_point_model=payload.get("charge_point_model", "Spoofed-Model"),
                charge_point_vendor=payload.get("charge_point_vendor", "Spoofed-Vendor"),
            )
        elif message_type == "Heartbeat":
            req = call.Heartbeat()
        elif message_type == "Authorize":
            req = call.Authorize(id_tag=payload.get("id_tag", "SPOOF"))
        elif message_type == "StartTransaction":
            req = call.StartTransaction(
                connector_id=payload.get("connector_id", 1),
                id_tag=payload.get("id_tag", "SPOOF"),
                meter_start=payload.get("meter_start", 0),
                timestamp=payload.get("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ")),
            )
        elif message_type == "MeterValues":
            req = call.MeterValues(
                connector_id=payload.get("connector_id", 1),
                transaction_id=payload.get("transaction_id", 9999),
                meter_value=payload.get("meter_value", []),
            )
        elif message_type == "StopTransaction":
            req = call.StopTransaction(
                transaction_id=payload.get("transaction_id", 9999),
                meter_stop=payload.get("meter_stop", 0),
                timestamp=payload.get("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ")),
                id_tag=payload.get("id_tag"),
            )
        else:
            raise ValueError(f"Unsupported spoof_command type: {message_type}")
        await cp.call(req)
    except Exception as exc:
        logger.warning("Spoofed command failed: %s", exc)
    finally:
        recv_task.cancel()
        with contextlib.suppress(Exception):
            await ws.close()


async def _send_malformed_payload(station_id: str) -> None:
    ws = await websockets.connect(
        f"{CSMS_URL}/{station_id}",
        subprotocols=["ocpp1.6"],
    )
    try:
        await ws.send("{bad_json:")
    except Exception as exc:
        logger.warning("Malformed payload send failed: %s", exc)
    finally:
        await ws.close()

# ================== AUTH ==================

api_key_header = APIKeyHeader(name="x-api-key", auto_error=False)
RATE_LIMIT_PER_HOUR = int(os.getenv("SIM_RATE_LIMIT_PER_HOUR", "5000"))
_rate_limit_state: Dict[str, Dict[str, float]] = {}


def _enforce_rate_limit(api_key: str) -> None:
    if RATE_LIMIT_PER_HOUR <= 0:
        return
    now = time.time()
    window = 3600
    state = _rate_limit_state.get(api_key)
    if not state or now - state["start"] > window:
        _rate_limit_state[api_key] = {"start": now, "count": 1}
        return
    if state["count"] >= RATE_LIMIT_PER_HOUR:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    state["count"] += 1


def get_current_user(api_key: Optional[str] = Security(api_key_header)) -> Dict[str, str]:
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    user = get_user_by_api_key(api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")
    _enforce_rate_limit(api_key)
    return user

# ================== ENERGY LOOP ==================

async def update_energy_and_usage():
    global TOTAL_ENERGY_KWH, TOTAL_EARNINGS
    INTERVAL = 5

    while True:
        await asyncio.sleep(INTERVAL)

        for sid in list(manager.tasks.keys()):
            task = manager.tasks.get(sid)
            if task and not task.done():
                kw = random.uniform(3, 22)
                manager.station_usage[sid] = round(kw, 2)

                delta = kw * (INTERVAL / 3600)
                manager.station_energy_kwh[sid] += delta
                TOTAL_ENERGY_KWH += delta
                TOTAL_EARNINGS += delta * CURRENT_PRICE_PER_KWH

# ================== STARTUP ==================

@app.on_event("startup")
async def startup():
    init_db()
    # Start Prometheus metrics server on port 9100
    try:
        start_http_server(9100)
        logger.info("Prometheus metrics server started on http://0.0.0.0:9100/metrics")
    except Exception as e:
        logger.warning(f"Could not start Prometheus metrics server on port 9100: {e}")
    
    asyncio.create_task(update_energy_and_usage())

# ================== APIs ==================

@app.get("/stations", response_model=List[StationInfo])
async def get_stations(user=Depends(get_current_user)):
    return manager.list_stations(user["id"])


@app.get("/totals")
async def get_totals(user=Depends(get_current_user)):
    return {
        "total_energy_kwh": round(TOTAL_ENERGY_KWH, 3),
        "total_earnings": round(TOTAL_EARNINGS, 2),
        "price_per_kwh": round(CURRENT_PRICE_PER_KWH, 2),
    }


@app.get("/metrics")
async def get_metrics():
    """Prometheus metrics endpoint."""
    return PlainTextResponse(get_metrics_text())


@app.get("/pricing")
async def get_price(user=Depends(get_current_user)):
    return {"price": CURRENT_PRICE_PER_KWH}


@app.post("/pricing")
async def set_price(req: PriceUpdate, user=Depends(get_current_user)):
    global CURRENT_PRICE_PER_KWH
    if req.price <= 0:
        raise HTTPException(400, "Invalid price")
    CURRENT_PRICE_PER_KWH = req.price
    return {"status": "ok"}


@app.post("/stations/start")
async def start_station(req: StartRequest, user=Depends(get_current_user)):
    try:
        await manager.start_station(user["id"], req.station_id, req.profile)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))


@app.post("/stations/stop")
async def stop_station(req: StopRequest, user=Depends(get_current_user)):
    try:
        await manager.stop_station(user["id"], req.station_id)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))


@app.post("/stations/{station_id}/battery_profile")
async def set_battery_profile(station_id: str, req: BatteryProfileRequest, user=Depends(get_current_user)):
    chargepoint = manager.station_chargepoints.get(station_id)
    if not chargepoint or manager.station_owners.get(station_id) != user["id"]:
        raise HTTPException(status_code=404, detail=f"Station {station_id} not found or not connected")
    try:
        chargepoint.update_battery_profile(
            capacity_kwh=req.capacity_kwh,
            soc_kwh=req.soc_kwh,
            temperature_c=req.temperature_c,
            max_charge_power_kw=req.max_charge_power_kw,
            tapering_enabled=req.tapering_enabled,
        )
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Battery profile update error for {station_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update battery profile")


@app.post("/stations/scale")
async def scale(req: ScaleRequest, user=Depends(get_current_user)):
    try:
        await manager.scale_to(user["id"], req.count, req.profile)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))


@app.get("/stations/{station_id}/logs")
async def get_station_logs(station_id: str, user=Depends(get_current_user)):
    """Get recent log entries for a specific station."""
    logs = manager.get_station_logs(user["id"], station_id)
    return {
        "station_id": station_id,
        "logs": logs,
        "count": len(logs),
    }


@app.get("/api/v1/history/{station_id}")
async def get_station_history(
    station_id: str,
    limit_logs: int = 200,
    limit_snapshots: int = 200,
    user=Depends(get_current_user),
):
    if station_id not in manager.get_user_station_ids(user["id"]):
        raise HTTPException(status_code=404, detail="Station not found")
    history = db_get_station_history(station_id, limit_logs=limit_logs, limit_snapshots=limit_snapshots)
    return {
        "station_id": station_id,
        "logs": history.get("logs", []),
        "energy_snapshots": history.get("energy_snapshots", []),
    }


@app.get("/api/v1/sessions")
async def get_sessions(limit: int = 200, station_id: Optional[str] = None, user=Depends(get_current_user)):
    if station_id and station_id not in manager.get_user_station_ids(user["id"]):
        raise HTTPException(status_code=404, detail="Station not found")
    station_ids = None if station_id else manager.get_user_station_ids(user["id"])
    sessions = list_sessions(limit=limit, station_id=station_id, station_ids=station_ids)
    return {
        "count": len(sessions),
        "sessions": sessions,
    }


@app.get("/api/v1/security/events")
async def get_security_events(limit: int = 100, user=Depends(get_current_user)):
    station_ids = set(manager.get_user_station_ids(user["id"]))
    events = [
        event_to_dict(event)
        for event in security_monitor.get_recent_events(limit=limit)
        if not station_ids or event.station_id in station_ids
    ]
    return {
        "count": len(events),
        "events": events,
    }


@app.get("/api/v1/security/stations/{station_id}/events")
async def get_security_events_for_station(station_id: str, user=Depends(get_current_user)):
    station_ids = set(manager.get_user_station_ids(user["id"]))
    if station_ids and station_id not in station_ids:
        raise HTTPException(status_code=404, detail="Station not found")
    events = [event_to_dict(event) for event in security_monitor.get_events_for_station(station_id)]
    return {
        "station_id": station_id,
        "count": len(events),
        "events": events,
    }


@app.get("/api/v1/security/stats")
async def get_security_stats(user=Depends(get_current_user)):
    station_ids = set(manager.get_user_station_ids(user["id"]))
    stats = {"by_type": {}, "by_severity": {}}
    for event in security_monitor.get_recent_events(limit=1000):
        if station_ids and event.station_id not in station_ids:
            continue
        stats["by_type"][event.event_type.value] = stats["by_type"].get(event.event_type.value, 0) + 1
        stats["by_severity"][event.severity] = stats["by_severity"].get(event.severity, 0) + 1
    return stats


@app.delete("/api/v1/security/clear")
async def clear_security_events(user=Depends(get_current_user)):
    security_monitor.clear_events()
    return {"status": "cleared"}


@app.get("/api/v1/security/flows")
async def get_security_flows(window_seconds: int = 60, user=Depends(get_current_user)):
    station_ids = set(manager.get_user_station_ids(user["id"]))
    snapshot = flow_tracker.get_counts_snapshot(window_seconds=window_seconds)
    filtered = {
        "global": snapshot.get("global", {}),
        "by_station": {
            station_id: counts
            for station_id, counts in snapshot.get("by_station", {}).items()
            if station_id in station_ids
        },
        "window_seconds": window_seconds,
    }
    return filtered


@app.post("/api/v1/security/rules/reload")
async def reload_security_rules(user=Depends(get_current_user)):
    rule_evaluator.reload_rules()
    return {"status": "reloaded"}


@app.post("/api/v1/security/attack")
async def trigger_security_attack(req: SecurityAttackRequest, user=Depends(get_current_user)):
    station_ids = set(manager.get_user_station_ids(user["id"]))
    if req.station_id not in station_ids and not req.allow_unowned:
        raise HTTPException(status_code=404, detail="Station not found")

    action = req.action
    if action == "inject_fault":
        if not req.type:
            raise HTTPException(status_code=400, detail="inject_fault requires type")
        if req.type == "HEARTBEAT_FLOOD":
            chargepoint = manager.station_chargepoints.get(req.station_id)
            if not chargepoint:
                raise HTTPException(status_code=404, detail="Station not connected")
            chargepoint.enable_heartbeat_flood(duration=req.duration)
            return {"status": "ok", "message": "Heartbeat flood enabled"}
        if req.type == "DUPLICATE_TRANSACTIONS":
            chargepoint = manager.station_chargepoints.get(req.station_id)
            if not chargepoint:
                raise HTTPException(status_code=404, detail="Station not connected")
            chargepoint.enable_duplicate_transactions(duration=req.duration)
            return {"status": "ok", "message": "Duplicate transactions enabled"}

        fault_type = FaultType(req.type)
        fault_manager.add_fault_rule(
            FaultRule(
                fault_type=fault_type,
                station_id=req.station_id,
                trigger_time=0,
                duration=req.duration,
                message_type=req.message_type,
            )
        )
        return {"status": "ok", "message": f"Fault injected: {fault_type.value}"}

    if action == "spoof_command":
        if not req.type:
            raise HTTPException(status_code=400, detail="spoof_command requires type")
        await _send_spoofed_command(
            station_id=req.station_id,
            message_type=req.type,
            payload=req.payload or {},
        )
        security_monitor.log_event(
            EventType.UNAUTHORIZED_COMMAND,
            req.station_id,
            f"Manual spoofed command sent: {req.type}",
            severity="high",
        )
        return {"status": "ok", "message": "Spoofed command sent"}

    if action == "tamper_payload":
        chargepoint = manager.station_chargepoints.get(req.station_id)
        if chargepoint:
            chargepoint.enable_tamper_payload(
                target_message=req.target_message,
                corruption_type=req.corruption_type or "truncate_field",
                duration=req.duration,
            )
        else:
            await _send_malformed_payload(req.station_id)
        security_monitor.log_event(
            EventType.MALFORMED_MESSAGE,
            req.station_id,
            "Manual payload tamper triggered",
            severity="high",
        )
        return {"status": "ok", "message": "Payload tamper triggered"}

    raise HTTPException(status_code=400, detail=f"Unsupported action: {action}")


# ================== SMARTCHARGING APIs ==================

@app.post("/stations/{station_id}/charging_profile", response_model=ChargingProfileResponse)
async def send_charging_profile(station_id: str, req: ChargingProfileRequest, user=Depends(get_current_user)):
    """
    Send a charging profile to a specific station.
    
    Args:
        station_id: Station identifier
        req: Profile request containing connector_id and profile dict
        
    Returns:
        Status of the operation
    """
    logger.info(f"API: Sending charging profile to {station_id}, connector {req.connector_id}")
    
    # Check if station exists
    chargepoint = manager.station_chargepoints.get(station_id)
    if not chargepoint or manager.station_owners.get(station_id) != user["id"]:
        logger.error(f"Station {station_id} not found")
        raise HTTPException(status_code=404, detail=f"Station {station_id} not found or not connected")
    
    try:
        # Send profile via CSMS
        result = await chargepoint.send_charging_profile_to_station(
            connector_id=req.connector_id,
            profile_dict=req.profile
        )
        
        profile_id = req.profile.get('chargingProfileId', 'unknown')
        
        if result.get('status') == 'Accepted':
            logger.info(f"Profile {profile_id} accepted by {station_id}")
            return ChargingProfileResponse(
                status="success",
                station_id=station_id,
                connector_id=req.connector_id,
                profile_id=profile_id,
                message=f"Charging profile {profile_id} sent successfully"
            )
        else:
            logger.warning(f"Profile {profile_id} rejected by {station_id}: {result.get('status')}")
            return ChargingProfileResponse(
                status="rejected",
                station_id=station_id,
                connector_id=req.connector_id,
                profile_id=profile_id,
                message=f"Profile rejected with status: {result.get('status')}",
                error=result.get('error')
            )
            
    except Exception as e:
        logger.error(f"Error sending profile to {station_id}: {e}")
        return ChargingProfileResponse(
            status="error",
            station_id=station_id,
            connector_id=req.connector_id,
            message="Failed to send charging profile",
            error=str(e)
        )


@app.get("/stations/{station_id}/composite_schedule", response_model=CompositeScheduleResponse)
async def get_composite_schedule(
    station_id: str,
    connector_id: int = Query(..., description="Connector ID"),
    duration: int = Query(..., description="Duration in seconds"),
    charging_rate_unit: str = Query(default="W", description="W or A"),
    user=Depends(get_current_user),
):
    """
    Request composite schedule from a station.
    
    Args:
        station_id: Station identifier
        connector_id: Connector ID
        duration: Duration in seconds
        charging_rate_unit: W (Watts) or A (Amps)
        
    Returns:
        Composite schedule or error
    """
    logger.info(f"API: Requesting composite schedule from {station_id}, connector {connector_id}")
    
    # Check if station exists
    chargepoint = manager.station_chargepoints.get(station_id)
    if not chargepoint or manager.station_owners.get(station_id) != user["id"]:
        logger.error(f"Station {station_id} not found")
        raise HTTPException(status_code=404, detail=f"Station {station_id} not found or not connected")
    
    try:
        # Request composite schedule
        result = await chargepoint.request_composite_schedule_from_station(
            connector_id=connector_id,
            duration=duration,
            charging_rate_unit=charging_rate_unit
        )
        
        if result.get('status') == 'Accepted':
            logger.info(f"Composite schedule retrieved from {station_id}")
            return CompositeScheduleResponse(
                status="success",
                station_id=station_id,
                connector_id=connector_id,
                schedule=result.get('schedule'),
                message="Composite schedule retrieved successfully"
            )
        else:
            logger.warning(f"Composite schedule request rejected by {station_id}")
            return CompositeScheduleResponse(
                status="rejected",
                station_id=station_id,
                connector_id=connector_id,
                message=f"Request rejected with status: {result.get('status')}",
                error=result.get('error')
            )
            
    except Exception as e:
        logger.error(f"Error requesting composite schedule from {station_id}: {e}")
        return CompositeScheduleResponse(
            status="error",
            station_id=station_id,
            connector_id=connector_id,
            message="Failed to request composite schedule",
            error=str(e)
        )


@app.delete("/stations/{station_id}/charging_profile")
async def clear_charging_profile(
    station_id: str,
    profile_id: Optional[int] = Query(None, description="Profile ID to clear"),
    connector_id: Optional[int] = Query(None, description="Connector ID"),
    purpose: Optional[str] = Query(None, description="Profile purpose"),
    stack_level: Optional[int] = Query(None, description="Stack level"),
    user=Depends(get_current_user),
):
    """
    Clear charging profiles from a station.
    
    Args:
        station_id: Station identifier
        profile_id: Optional specific profile ID
        connector_id: Optional connector ID filter
        purpose: Optional purpose filter
        stack_level: Optional stack level filter
        
    Returns:
        Status of the operation
    """
    logger.info(f"API: Clearing charging profiles from {station_id}")
    
    # Check if station exists
    chargepoint = manager.station_chargepoints.get(station_id)
    if not chargepoint or manager.station_owners.get(station_id) != user["id"]:
        logger.error(f"Station {station_id} not found")
        raise HTTPException(status_code=404, detail=f"Station {station_id} not found or not connected")
    
    try:
        # Clear profiles
        result = await chargepoint.clear_charging_profile_from_station(
            profile_id=profile_id,
            connector_id=connector_id,
            purpose=purpose,
            stack_level=stack_level
        )
        
        if result.get('status') == 'Accepted':
            logger.info(f"Profiles cleared from {station_id}")
            return {
                "status": "success",
                "station_id": station_id,
                "message": "Charging profiles cleared successfully",
                "filters": {
                    "profile_id": profile_id,
                    "connector_id": connector_id,
                    "purpose": purpose,
                    "stack_level": stack_level
                }
            }
        else:
            logger.warning(f"Clear profile request rejected by {station_id}")
            return {
                "status": "rejected",
                "station_id": station_id,
                "message": f"Request rejected with status: {result.get('status')}",
                "error": result.get('error')
            }
            
    except Exception as e:
        logger.error(f"Error clearing profiles from {station_id}: {e}")
        return {
            "status": "error",
            "station_id": station_id,
            "message": "Failed to clear charging profiles",
            "error": str(e)
        }


@app.post("/stations/{station_id}/test_profiles", response_model=TestProfileResponse)
async def send_test_profile(station_id: str, req: TestProfileRequest, user=Depends(get_current_user)):
    """
    Generate and send a test charging profile based on a scenario.
    
    Scenarios:
    - peak_shaving: Limit station max power (requires max_power_w)
    - time_of_use: Daily recurring with peak/off-peak hours (requires off_peak_w, peak_w, peak_start_hour, peak_end_hour)
    - energy_cap: Transaction-specific energy limit (requires transaction_id, max_energy_wh, duration_seconds, power_limit_w)
    
    Args:
        station_id: Station identifier
        req: Test profile request with scenario and parameters
        
    Returns:
        Generated profile and send status
    """
    logger.info(f"API: Generating test profile '{req.scenario}' for {station_id}")
    
    # Check if station exists
    chargepoint = manager.station_chargepoints.get(station_id)
    if not chargepoint or manager.station_owners.get(station_id) != user["id"]:
        logger.error(f"Station {station_id} not found")
        raise HTTPException(status_code=404, detail=f"Station {station_id} not found or not connected")
    
    try:
        # Generate profile based on scenario
        if req.scenario == "peak_shaving":
            if req.max_power_w is None:
                raise HTTPException(status_code=400, detail="max_power_w is required for peak_shaving")
            profile = create_charge_point_max_profile(
                profile_id=1,
                max_power_w=req.max_power_w
            )
            
        elif req.scenario == "time_of_use":
            if any(p is None for p in [req.off_peak_w, req.peak_w, req.peak_start_hour, req.peak_end_hour]):
                raise HTTPException(
                    status_code=400, 
                    detail="off_peak_w, peak_w, peak_start_hour, peak_end_hour required for time_of_use"
                )
            profile = create_time_of_use_profile(
                profile_id=2,
                off_peak_w=req.off_peak_w,
                peak_w=req.peak_w,
                peak_start_hour=req.peak_start_hour,
                peak_end_hour=req.peak_end_hour
            )
            
        elif req.scenario == "energy_cap":
            if any(p is None for p in [req.transaction_id, req.max_energy_wh, req.duration_seconds, req.power_limit_w]):
                raise HTTPException(
                    status_code=400, 
                    detail="transaction_id, max_energy_wh, duration_seconds, power_limit_w required for energy_cap"
                )
            profile = create_energy_cap_profile(
                profile_id=3,
                transaction_id=req.transaction_id,
                max_energy_wh=req.max_energy_wh,
                duration_seconds=req.duration_seconds,
                power_limit_w=req.power_limit_w
            )
            
        else:
            raise HTTPException(
                status_code=400, 
                detail=f"Unknown scenario '{req.scenario}'. Valid: peak_shaving, time_of_use, energy_cap"
            )
        
        logger.info(f"Generated {req.scenario} profile: {profile.get('chargingProfileId')}")
        
        # Send profile to station
        result = await chargepoint.send_charging_profile_to_station(
            connector_id=req.connector_id,
            profile_dict=profile
        )
        
        send_status = result.get('status', 'Unknown')
        
        return TestProfileResponse(
            status="success" if send_status == "Accepted" else "rejected",
            station_id=station_id,
            scenario=req.scenario,
            profile=profile,
            send_status=send_status,
            message=f"Test profile generated and sent with status: {send_status}",
            error=result.get('error')
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating/sending test profile to {station_id}: {e}")
        return TestProfileResponse(
            status="error",
            station_id=station_id,
            scenario=req.scenario,
            profile={},
            send_status="Error",
            message="Failed to generate or send test profile",
            error=str(e)
        )


@app.post("/admin/users")
async def create_user_admin(req: UserCreateRequest, user=Depends(get_current_user)):
    init_db()
    existing = get_user_by_email(req.email)
    if existing:
        return {"email": existing["email"], "api_key": existing["api_key"]}
    api_key = secrets.token_urlsafe(32)
    user = create_user(req.email, api_key)
    return {"email": user["email"], "api_key": user["api_key"]}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
