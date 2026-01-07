import asyncio
import random
from contextlib import suppress
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, Query
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

    def list_stations(self) -> List[StationInfo]:
        result = []
        for sid, task in self.tasks.items():
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

    async def start_station(self, station_id: str, profile_name: str):
        if station_id in self.tasks and not self.tasks[station_id].done():
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

    async def stop_station(self, station_id: str):
        task = self.tasks.get(station_id)
        if not task:
            return

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

        self.station_usage[station_id] = 0.0

    async def scale_to(self, target_count: int, profile_name: str = "default"):
        current_ids = sorted(self.tasks.keys())
        current_count = len(current_ids)

        # Stop all existing stations first
        for sid in current_ids:
            await self.stop_station(sid)
        
        # Create new stations with the specified profile
        for i in range(1, target_count + 1):
            sid = f"PY-SIM-{i:04d}"
            await self.start_station(sid, profile_name)

    def get_station_logs(self, station_id: str) -> List[str]:
        """
        Get recent log entries for a specific station.
        
        Args:
            station_id: The station identifier
            
        Returns:
            List of recent log entries, empty list if station not found
        """
        chargepoint = self.station_chargepoints.get(station_id)
        if not chargepoint:
            return []
        return chargepoint.get_logs()

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
    # Start Prometheus metrics server on port 9100
    try:
        start_http_server(9100)
        logger.info("Prometheus metrics server started on http://0.0.0.0:9100/metrics")
    except Exception as e:
        logger.warning(f"Could not start Prometheus metrics server on port 9100: {e}")
    
    asyncio.create_task(update_energy_and_usage())

# ================== APIs ==================

@app.get("/stations", response_model=List[StationInfo])
async def get_stations():
    return manager.list_stations()


@app.get("/totals")
async def get_totals():
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
async def get_price():
    return {"price": CURRENT_PRICE_PER_KWH}


@app.post("/pricing")
async def set_price(req: PriceUpdate):
    global CURRENT_PRICE_PER_KWH
    if req.price <= 0:
        raise HTTPException(400, "Invalid price")
    CURRENT_PRICE_PER_KWH = req.price
    return {"status": "ok"}


@app.post("/stations/start")
async def start_station(req: StartRequest):
    await manager.start_station(req.station_id, req.profile)
    return {"status": "ok"}


@app.post("/stations/stop")
async def stop_station(req: StopRequest):
    await manager.stop_station(req.station_id)
    return {"status": "ok"}


@app.post("/stations/scale")
async def scale(req: ScaleRequest):
    await manager.scale_to(req.count, req.profile)
    return {"status": "ok"}


@app.get("/stations/{station_id}/logs")
async def get_station_logs(station_id: str):
    """Get recent log entries for a specific station."""
    logs = manager.get_station_logs(station_id)
    return {
        "station_id": station_id,
        "logs": logs,
        "count": len(logs),
    }


# ================== SMARTCHARGING APIs ==================

@app.post("/stations/{station_id}/charging_profile", response_model=ChargingProfileResponse)
async def send_charging_profile(station_id: str, req: ChargingProfileRequest):
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
    if not chargepoint:
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
    charging_rate_unit: str = Query(default="W", description="W or A")
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
    if not chargepoint:
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
    stack_level: Optional[int] = Query(None, description="Stack level")
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
    if not chargepoint:
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
async def send_test_profile(station_id: str, req: TestProfileRequest):
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
    if not chargepoint:
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


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
