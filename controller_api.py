import asyncio
import random
from contextlib import suppress
from typing import Dict, List

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from prometheus_client import start_http_server
import logging

from profiles import DEFAULT_PROFILES, StationProfile
from station import simulate_station
from metrics import (
    get_metrics_text,
    set_stations_total,
    set_stations_active,
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


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
