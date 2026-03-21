import asyncio
import contextlib
import os
import secrets
from datetime import datetime
from contextlib import suppress
from typing import Dict, List, Optional, Sequence, cast

from fastapi import FastAPI, HTTPException, Request, Query, Depends, Security
from fastapi.security import APIKeyHeader
from station import SimulatedChargePoint
from protocol_station import simulate_station
from accounting import (
    get_price as accounting_get_price,
    get_totals as accounting_get_totals,
    reset_totals as accounting_reset_totals,
    set_price as accounting_set_price,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from prometheus_client import start_http_server
import logging
import csv
from pathlib import Path
from jinja2 import TemplateNotFound

from profiles import DEFAULT_PROFILES, StationProfile
from determinism_guards import assert_no_defaults_or_fallbacks, assert_no_wall_clock_usage
from replay_mode import (
    ReplayMode,
    get_replay_mode,
    is_real_csv_entry_active,
    is_real_csv_mode,
    is_replay_mode_explicit,
    is_strict_mode,
    log_mode_banner,
    set_replay_mode,
)
from live_metrics import get_live_metrics, reset_live_metrics
from protocol_station import simulate_station
from state_version import get_state_version, increment_state_version
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
from ocpp_compat import build_call
from db import (
    init_db,
    get_latest_energy_snapshot,
    get_station_history as db_get_station_history,
    list_sessions,
    get_security_events_paginated,
    get_security_event_type_stats,
    create_user,
    get_user_by_api_key,
    get_user_by_email,
)
from security_monitor import event_to_dict, security_monitor
from security_monitor import EventType
from fault_injector import FaultRule, FaultType, fault_manager
from security_detection import flow_tracker, rule_evaluator
from replay_integration import (
    run_replay_pipeline,
    run_real_csv_replay,
    pause_real_csv_replay,
    resume_real_csv_replay,
    is_real_csv_replay_paused,
)
from csv_cleaner import clean_file, resolve_timezone, OUTPUT_FILE, META_FILE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("controller_api")

_REAL_CSV_TASK: Optional[asyncio.Task] = None
_REAL_CSV_STATUS: str = "idle"
_REAL_CSV_LAST_ERROR: Optional[str] = None
_REAL_CSV_LAST_REPORT: Optional[dict] = None

if not is_replay_mode_explicit():
    set_replay_mode(ReplayMode.STRICT)
assert_no_defaults_or_fallbacks()
try:
    assert_no_wall_clock_usage()
except RuntimeError as exc:
    if os.getenv("ENFORCE_WALL_CLOCK_GUARD", "0") == "1":
        raise
    logger.warning("Wall-clock guard warning ignored: %s", exc)

CSMS_URL = "ws://localhost:9000/ocpp"  # keep it, but don't let it block

# ================== MODELS ==================

class StationInfo(BaseModel):
    station_id: str
    profile: str
    running: bool
    usage_kw: float
    energy_kwh: float
    soc_percent: Optional[float] = None
    live_metrics_active: Optional[bool] = None
    # Smart charging info
    max_energy_kwh: float
    charge_if_price_below: float
    allow_peak: bool
    energy_percent: float  # Percentage of max energy cap
    lat: Optional[float] = None
    lng: Optional[float] = None


class ScaleRequest(BaseModel):
    count: int
    profile: str


class StartRequest(BaseModel):
    station_id: str
    profile: str


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
    created_at: str


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


class ReplayRunRequest(BaseModel):
    csv_directory: Optional[str] = None
    csv_files: Optional[List[str]] = None
    timezone_name: str = "UTC"
    voltage: float
    expected_duration_tolerance_seconds: float = 1.0
    expected_energy_tolerance_kwh: float = 0.1
    strict: bool = True
    replay_mode: Optional[str] = None


# ================== SMARTCHARGING MODELS ==================

class ChargingProfileRequest(BaseModel):
    """Request to send a charging profile to a station."""
    connector_id: int = Field(..., description="Connector ID (1-N, or 0 for station-wide)")
    profile: dict = Field(..., description="OCPP charging profile dict")


class CompositeScheduleRequest(BaseModel):
    """Request to get composite schedule from a station."""
    connector_id: int = Field(..., description="Connector ID")
    duration: int = Field(..., description="Duration in seconds")
    charging_rate_unit: str = Field(..., description="W or A")


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
        self.station_chargepoints: Dict[str, SimulatedChargePoint] = {}
        self.station_owners: Dict[str, int] = {}

    def list_stations(self, user_id: int) -> List[StationInfo]:
        result = []
        for sid, task in self.tasks.items():
            if self.station_owners.get(sid) != user_id:
                continue
            profile_name = self.station_profiles.get(sid)
            profile = self.profiles.get(profile_name) if profile_name else None
            energy = self.station_energy_kwh.get(sid, 0.0)
            usage_kw = self.station_usage.get(sid, 0.0)
            soc_percent = None
            live_metrics_active = None
            if is_real_csv_mode():
                live = get_live_metrics(sid)
                if live is not None:
                    energy = float(live.get("energy_kwh", energy))
                    usage_kw = float(live.get("power_kw", usage_kw))
                    soc_percent = live.get("soc_percent")
                    live_metrics_active = True
                else:
                    latest_energy = get_latest_energy_snapshot(sid)
                    if latest_energy is not None:
                        energy = float(latest_energy)
                        usage_kw = 0.0
                        soc_percent = None
                        live_metrics_active = True
                    else:
                        energy = 0.0
                        usage_kw = 0.0
                        soc_percent = None
                        live_metrics_active = False
            energy_pct = 0.0
            if profile:
                max_energy = profile.max_energy_kwh
                energy_pct = (energy / max_energy * 100) if max_energy > 0 else 0
            
            import hashlib
            
            # Deterministic pseudo-randomness based on station_id
            hash_val = int(hashlib.md5(sid.encode()).hexdigest(), 16)
            
            # Using data from Completed_Bookings_December.csv (Delhi, Solan, Shimla)
            lat, lng = 28.6139, 77.2090  # Default to New Delhi coordinates
            
            if "HP" in sid or "HEVN" in sid: 
                if "SHIMLA" in sid or "RTO" in sid:
                    lat, lng = 31.1048, 77.1734  # Shimla
                else:
                    lat, lng = 30.9045, 77.0967  # Solan
            elif "PASCHIM" in sid:
                lat, lng = 28.6692, 77.1008  # Paschim Vihar, Delhi
            elif "DELITE" in sid:
                lat, lng = 28.6415, 77.2373  # Delite Cinema area, Delhi
            elif "PY-SIM" in sid:
                # Spread simulated scaled stations across a grid around Delhi NCR
                lat = 28.4 + (hash_val % 400) / 1000.0  # 28.4 to 28.8
                lng = 76.8 + ((hash_val // 1000) % 600) / 1000.0  # 76.8 to 77.4
            
            # Deterministic jitter for ALL stations so pins never perfectly overlap but stay static
            jitter_lat = ((hash_val // 1000000) % 100) / 10000.0 - 0.005
            jitter_lng = ((hash_val // 100000000) % 100) / 10000.0 - 0.005
            
            lat += jitter_lat
            lng += jitter_lng

            result.append(
                StationInfo(
                    station_id=sid,
                    profile=profile_name or "no-profile",
                    running=not task.done(),
                    usage_kw=round(usage_kw, 2),
                    energy_kwh=round(energy, 3),
                    soc_percent=round(float(soc_percent), 1) if soc_percent is not None else None,
                    live_metrics_active=live_metrics_active,
                    max_energy_kwh=profile.max_energy_kwh if profile else 0.0,
                    charge_if_price_below=profile.charge_if_price_below if profile else 0.0,
                    allow_peak=profile.allow_peak if profile else False,
                    energy_percent=round(energy_pct, 1),
                    lat=lat,
                    lng=lng,
                )
            )
        return result

    async def start_station(self, user_id: int, station_id: str, profile_name: str):
        if is_strict_mode():
            raise RuntimeError("STRICT mode forbids live station execution")
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
                live_mode = os.getenv("SIM_LIVE_MODE", "1") == "1"
                if live_mode:
                    await simulate_station(
                        station_id,
                        self.csms_url,
                        profile,
                        current_price=float(accounting_get_price()),
                        on_chargepoint_ready=on_chargepoint_ready,
                    )
                    return
                # Deterministic/replay-only mode: disallow time-driven stations.
                raise RuntimeError(
                    "Time-driven station execution is disabled. Use replay-driven execution."
                )
            except Exception as exc:
                logger.warning("Station %s failed to connect/start: %s", station_id, exc)
                raise

        task = asyncio.create_task(safe_sim())

        self.tasks[station_id] = task
        self.station_profiles[station_id] = profile_name
        self.station_usage[station_id] = 0.0
        self.station_energy_kwh[station_id] = 0.0
        self.station_owners[station_id] = user_id
        increment_state_version()

    async def stop_station(self, user_id: int, station_id: str):
        task = self.tasks.get(station_id)
        if not task:
            return
        if self.station_owners.get(station_id) != user_id:
            raise ValueError("Station not owned by user")

        task.cancel()
        try:
            with suppress(asyncio.CancelledError):
                await task
        except Exception as exc:
            logger.warning("Stop station %s raised: %s", station_id, exc)
        finally:
            self.station_usage[station_id] = 0.0
            self.tasks.pop(station_id, None)
            self.station_profiles.pop(station_id, None)
            self.station_energy_kwh.pop(station_id, None)
            self.station_chargepoints.pop(station_id, None)
            self.station_owners.pop(station_id, None)
            increment_state_version()

    async def scale_to(self, user_id: int, target_count: int, profile_name: str):
        current_ids = sorted(
            sid for sid, owner in self.station_owners.items() if owner == user_id
        )
        current_count = len(current_ids)

        # Stop all existing stations first
        for sid in current_ids:
            try:
                await self.stop_station(user_id, sid)
            except Exception as exc:
                logger.warning("Scale stop failed for %s: %s", sid, exc)
        
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

    async def reset_user_state(self, user_id: int) -> None:
        station_ids = self.get_user_station_ids(user_id)
        for station_id in station_ids:
            if station_id in self.tasks:
                try:
                    await self.stop_station(user_id, station_id)
                except Exception as exc:
                    logger.warning("Reset stop failed for %s: %s", station_id, exc)
                    continue
            self.station_usage.pop(station_id, None)
            self.station_profiles.pop(station_id, None)
            self.station_energy_kwh.pop(station_id, None)
            self.station_chargepoints.pop(station_id, None)
            self.station_owners.pop(station_id, None)

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
        subprotocols=["ocpp1.6"],  # type: ignore[arg-type]
    )
    cp = CP(station_id, ws)
    recv_task = asyncio.create_task(cp.start())
    try:
        if message_type == "BootNotification":
            req = build_call(
                "BootNotification",
                charge_point_model=payload.get("charge_point_model", "Spoofed-Model"),
                charge_point_vendor=payload.get("charge_point_vendor", "Spoofed-Vendor"),
            )
        elif message_type == "Heartbeat":
            req = build_call("Heartbeat")
        elif message_type == "Authorize":
            req = build_call("Authorize", id_tag=payload.get("id_tag", "SPOOF"))
        elif message_type == "StartTransaction":
            raise RuntimeError("Transaction outside replay forbidden")
        elif message_type == "MeterValues":
            raise RuntimeError(
                "Direct MeterValues emission is forbidden. Use replay."
            )
        elif message_type == "StopTransaction":
            raise RuntimeError("Transaction outside replay forbidden")
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
        subprotocols=["ocpp1.6"],  # type: ignore[arg-type]
    )
    try:
        await ws.send("{bad_json:")
    except Exception as exc:
        logger.warning("Malformed payload send failed: %s", exc)
    finally:
        await ws.close()

# ================== AUTH ==================

api_key_header = APIKeyHeader(name="x-api-key", auto_error=False)
RATE_LIMIT_PER_HOUR = int(os.getenv("SIM_RATE_LIMIT_PER_HOUR", "0"))
_rate_limit_state: Dict[str, int] = {}


def _enforce_rate_limit(api_key: str) -> None:
    if os.getenv("SIM_DISABLE_RATE_LIMIT", "1") == "1":
        return
    if RATE_LIMIT_PER_HOUR <= 0:
        return
    count = _rate_limit_state.get(api_key, 0)
    if count >= RATE_LIMIT_PER_HOUR:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    _rate_limit_state[api_key] = count + 1


def get_current_user(api_key: Optional[str] = Security(api_key_header)) -> Dict[str, str]:
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    user = get_user_by_api_key(api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")
    _enforce_rate_limit(api_key)
    return user


def _ensure_live_station_mode() -> None:
    if is_real_csv_mode():
        return
    if os.getenv("REPLAY_MODE"):
        raise HTTPException(
            status_code=409,
            detail=(
                "Live station actions require REPLAY_MODE=REAL_CSV. "
                "Restart the API with REPLAY_MODE=REAL_CSV."
            ),
        )
    set_replay_mode(ReplayMode.REAL_CSV)
    logger.warning("Live station execution requested; switching to REAL_CSV mode")


def _write_cleaned_csv(output_dir: Path, input_paths: Sequence[Path]) -> Path:
    target_tz = resolve_timezone()
    if target_tz is None:
        raise HTTPException(status_code=500, detail="Target timezone could not be resolved")

    energy_unit = os.environ.get("CSV_ENERGY_UNIT")
    energy_column = os.environ.get("CSV_ENERGY_COLUMN")
    energy_unit = energy_unit.strip() if energy_unit else None
    energy_column = energy_column.strip() if energy_column else None
    # Backward-compatible defaults for dashboard replay.
    # csv_cleaner still enforces kWh-only units.
    if not energy_unit:
        energy_unit = "kwh"
    if not energy_column:
        energy_column = "Energy Consumed"
    max_energy_kwh_raw = os.getenv("CSV_MAX_ENERGY_KWH")
    max_avg_power_raw = os.getenv("CSV_MAX_AVG_POWER_KW")
    max_energy_kwh = float(max_energy_kwh_raw) if max_energy_kwh_raw else None
    max_avg_power_kw = float(max_avg_power_raw) if max_avg_power_raw else None

    cleaned_rows = []
    for path in input_paths:
        if not path.exists():
            raise HTTPException(status_code=400, detail=f"CSV not found: {path}")
        try:
            cleaned_rows.extend(
                clean_file(
                    path,
                    target_tz,
                    energy_column=energy_column,
                    energy_unit=energy_unit,
                    max_energy_kwh=max_energy_kwh,
                    max_avg_power_kw=max_avg_power_kw,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    if not cleaned_rows:
        raise HTTPException(status_code=400, detail="No valid sessions found in CSV input")

    output_dir.mkdir(parents=True, exist_ok=True)
    cleaned_path = output_dir / OUTPUT_FILE
    with cleaned_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["station_id", "start_time", "end_time", "total_energy_kWh"])
        for row in cleaned_rows:
            writer.writerow([
                row.station_id,
                row.start_time,
                row.end_time,
                row.total_energy_kWh,
            ])

    meta_path = output_dir / META_FILE
    meta_path.write_text("CLEANED_CSV_V1", encoding="utf-8")
    return cleaned_path


def _prepare_cleaned_paths(
    csv_directory: Optional[str],
    csv_files: Optional[Sequence[str]],
) -> List[Path]:
    if csv_files:
        raw_paths = [Path(path) for path in csv_files]
        if all(path.name == OUTPUT_FILE for path in raw_paths):
            return raw_paths
        output_dir = Path(csv_directory) if csv_directory else Path.cwd()
        return [_write_cleaned_csv(output_dir, raw_paths)]
    if csv_directory:
        output_dir = Path(csv_directory)
        raw_paths = sorted(
            path
            for path in output_dir.glob("*.csv")
            if path.name != OUTPUT_FILE
        )
        if raw_paths:
            return [_write_cleaned_csv(output_dir, raw_paths)]
        cleaned_path = output_dir / OUTPUT_FILE
        if cleaned_path.exists():
            return [cleaned_path]
        raise HTTPException(status_code=400, detail=f"No CSV files found in directory: {output_dir}")
    return [Path.cwd() / OUTPUT_FILE]


# ================== ENERGY LOOP ==================

def get_session_metrics_placeholder(
        station_id: str,
        interval_seconds: int,
) -> Optional[Dict[str, float]]:
        """
        Placeholder for CSV-driven metrics.

        TODO: TO BE DRIVEN BY REAL CSV SESSION DATA
        Expected return keys:
            - power_kw
            - energy_delta_kwh
        """
        return None

async def update_energy_and_usage():
    raise RuntimeError(
        "Time-driven energy updates are forbidden. Use replay-driven metrics."
    )

# ================== STARTUP ==================

@app.on_event("startup")
async def startup():
    init_db()
    log_mode_banner(logger)
    # Start Prometheus metrics server on port 9100
    try:
        start_http_server(9100)
        logger.info("Prometheus metrics server started on http://0.0.0.0:9100/metrics")
    except Exception as e:
        logger.warning(f"Could not start Prometheus metrics server on port 9100: {e}")
    
    # Time-driven updates are forbidden under replay-only determinism.

# ================== APIs ==================

@app.get("/stations", response_model=List[StationInfo])
async def get_stations(user=Depends(get_current_user)):
    return manager.list_stations(user["id"])


@app.get("/stations/state")
async def get_stations_state(user=Depends(get_current_user)):
    return {
        "state_version": get_state_version(),
        "stations": manager.list_stations(user["id"]),
    }


@app.get("/totals")
async def get_totals(user=Depends(get_current_user)):
    total_energy_kwh, total_earnings = accounting_get_totals()
    return {
        "total_energy_kwh": round(float(total_energy_kwh), 3),
        "total_earnings": round(float(total_earnings), 2),
        "price_per_kwh": round(float(accounting_get_price()), 2),
        "replay_mode": get_replay_mode().value,
        "replay_active": bool(is_real_csv_entry_active()) if is_real_csv_mode() else False,
        "replay_paused": bool(is_real_csv_replay_paused()) if is_real_csv_mode() else False,
        "state_version": get_state_version(),
    }


@app.get("/metrics")
async def get_metrics():
    """Prometheus metrics endpoint."""
    return PlainTextResponse(get_metrics_text())


@app.get("/pricing")
async def get_price(user=Depends(get_current_user)):
    return {"price": float(accounting_get_price())}


@app.post("/pricing")
async def set_price(req: PriceUpdate, user=Depends(get_current_user)):
    if req.price <= 0:
        raise HTTPException(400, "Invalid price")
    accounting_set_price(req.price)
    return {"status": "ok"}


@app.post("/stations/start")
async def start_station(req: StartRequest, user=Depends(get_current_user)):
    try:
        _ensure_live_station_mode()
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
    if is_real_csv_mode() and is_real_csv_entry_active():
        raise HTTPException(
            status_code=409,
            detail="Profile mutation is forbidden during REAL_CSV execution",
        )
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
        _ensure_live_station_mode()
        await manager.scale_to(user["id"], req.count, req.profile)
        return {"status": "ok"}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.exception("Scale failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to scale stations")


@app.post("/replay/run")
async def run_replay(req: ReplayRunRequest, user=Depends(get_current_user)):
    if not req.csv_directory and not req.csv_files:
        raise HTTPException(status_code=400, detail="csv_directory or csv_files required")
    if not manager.get_user_station_ids(user["id"]):
        raise HTTPException(status_code=400, detail="No stations running for user")
    mode_value = (req.replay_mode or "STRICT").strip().upper()
    try:
        set_replay_mode(ReplayMode(mode_value))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid replay_mode; use STRICT or REAL_CSV")
    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "Replay mode cannot be switched at runtime. "
                "Restart the API with REPLAY_MODE set to the desired mode."
            ),
        ) from exc

    if is_strict_mode() and req.strict is False:
        raise HTTPException(status_code=400, detail="STRICT mode validation cannot be weakened")

    if is_real_csv_mode():
        raise HTTPException(status_code=400, detail="Use /replay/real-csv for REAL_CSV mode")

    def chargepoint_provider(station_id: str) -> Optional[SimulatedChargePoint]:
        if manager.station_owners.get(station_id) != user["id"]:
            return None
        return manager.station_chargepoints.get(station_id)

    try:
        report = await run_replay_pipeline(
            chargepoint_provider=chargepoint_provider,
            expected_duration_tolerance_seconds=req.expected_duration_tolerance_seconds,
            expected_energy_tolerance_kwh=req.expected_energy_tolerance_kwh,
            csv_directory=req.csv_directory,
            csv_files=req.csv_files,
            timezone_name=req.timezone_name,
            voltage=req.voltage,
            strict=True,
        )
        return {
            "status": "ok",
            "results": report.to_payload(),
        }
    except Exception as exc:
        logger.exception("Replay failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/replay/real-csv")
async def run_replay_real_csv(req: ReplayRunRequest, user=Depends(get_current_user)):
    if not req.csv_directory and not req.csv_files:
        raise HTTPException(status_code=400, detail="csv_directory or csv_files required")

    if os.getenv("REPLAY_MODE") and not is_real_csv_mode():
        raise HTTPException(
            status_code=409,
            detail=(
                "REAL_CSV replay requires REPLAY_MODE=REAL_CSV at startup. "
                "Restart the API with REPLAY_MODE=REAL_CSV."
            ),
        )
    set_replay_mode(ReplayMode.REAL_CSV)

    await manager.reset_user_state(user["id"])
    reset_live_metrics()
    accounting_reset_totals()

    total_energy_kwh, total_earnings = accounting_get_totals()
    if total_energy_kwh != 0 or total_earnings != 0:
        raise HTTPException(
            status_code=500,
            detail="Replay boundary reset failed: totals not zero",
        )

    cleaned_paths = _prepare_cleaned_paths(req.csv_directory, req.csv_files)

    station_ids: List[str] = []
    for path in cleaned_paths:
        if not path.exists():
            raise HTTPException(status_code=400, detail=f"Cleaned CSV not found: {path}")
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                continue
            for row in reader:
                station_id = str(row.get("station_id") or "").strip()
                if station_id:
                    station_ids.append(station_id)
    station_ids = sorted(set(station_ids))
    for station_id in station_ids:
        await manager.start_station(user["id"], station_id, "default")

    missing: List[str] = []
    for _ in range(50):
        missing = [
            station_id
            for station_id in station_ids
            if manager.station_chargepoints.get(station_id) is None
        ]
        if not missing:
            break
        await asyncio.sleep(0.1)
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"Stations not ready for replay: {', '.join(missing)}",
        )

    def chargepoint_provider(station_id: str) -> Optional[SimulatedChargePoint]:
        if manager.station_owners.get(station_id) != user["id"]:
            return None
        return manager.station_chargepoints.get(station_id)

    global _REAL_CSV_TASK, _REAL_CSV_STATUS, _REAL_CSV_LAST_ERROR, _REAL_CSV_LAST_REPORT
    if _REAL_CSV_TASK and not _REAL_CSV_TASK.done():
        raise HTTPException(status_code=409, detail="REAL_CSV replay already running")

    async def _runner() -> None:
        nonlocal cleaned_paths
        global _REAL_CSV_STATUS, _REAL_CSV_LAST_ERROR, _REAL_CSV_LAST_REPORT
        _REAL_CSV_STATUS = "running"
        _REAL_CSV_LAST_ERROR = None
        _REAL_CSV_LAST_REPORT = None
        try:
            report = await run_real_csv_replay(
                chargepoint_provider=chargepoint_provider,
                expected_duration_tolerance_seconds=req.expected_duration_tolerance_seconds,
                expected_energy_tolerance_kwh=req.expected_energy_tolerance_kwh,
                csv_directory=str(cleaned_paths[0].parent) if cleaned_paths else req.csv_directory,
                csv_files=[str(path) for path in cleaned_paths],
                timezone_name=req.timezone_name,
                voltage=req.voltage,
            )
            _REAL_CSV_LAST_REPORT = report.to_payload()
            _REAL_CSV_STATUS = "completed"
        except Exception as exc:
            _REAL_CSV_LAST_ERROR = str(exc)
            _REAL_CSV_STATUS = "failed"
            logger.exception("REAL_CSV replay failed: %s", exc)

    _REAL_CSV_TASK = asyncio.create_task(_runner())
    return {"status": "running"}


@app.get("/replay/status")
async def replay_status(user=Depends(get_current_user)):
    return {
        "status": _REAL_CSV_STATUS,
        "error": _REAL_CSV_LAST_ERROR,
        "results": _REAL_CSV_LAST_REPORT,
    }


@app.post("/replay/pause")
async def pause_replay(user=Depends(get_current_user)):
    if not is_real_csv_mode():
        raise HTTPException(status_code=400, detail="Pause supported only in REAL_CSV mode")
    try:
        pause_real_csv_replay()
        return {"status": "ok", "paused": True}
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/replay/resume")
async def resume_replay(user=Depends(get_current_user)):
    if not is_real_csv_mode():
        raise HTTPException(status_code=400, detail="Resume supported only in REAL_CSV mode")
    try:
        resume_real_csv_replay()
        return {"status": "ok", "paused": False}
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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


@app.get("/security/events")
async def get_security_events_paginated_api(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    severity: Optional[int] = Query(None, ge=1, le=10),
    charge_point_id: Optional[str] = Query(None, min_length=1, max_length=100),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    user=Depends(get_current_user),
):
    start_dt: Optional[datetime] = None
    end_dt: Optional[datetime] = None
    try:
        if start_date:
            start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        if end_date:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        if start_dt and end_dt and start_dt > end_dt:
            raise HTTPException(status_code=400, detail="start_date must be <= end_date")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ISO timestamp in start_date or end_date")

    station_ids = set(manager.get_user_station_ids(user["id"]))
    if charge_point_id and station_ids and charge_point_id not in station_ids:
        return {
            "page": page,
            "limit": limit,
            "total": 0,
            "data": [],
        }

    result = get_security_events_paginated(
        page=page,
        limit=limit,
        severity=severity,
        charge_point_id=charge_point_id,
        charge_point_ids=None if charge_point_id else (list(station_ids) if station_ids else None),
        start_date=start_dt,
        end_date=end_dt,
    )
    return result


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


@app.get("/security/events/stats")
async def get_security_stats_grouped(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    charge_point_id: Optional[str] = Query(None, min_length=1, max_length=100),
    user=Depends(get_current_user),
):
    start_dt: Optional[datetime] = None
    end_dt: Optional[datetime] = None
    try:
        if start_date:
            start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        if end_date:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        if start_dt and end_dt and start_dt > end_dt:
            raise HTTPException(status_code=400, detail="start_date must be <= end_date")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ISO timestamp in start_date or end_date")

    station_ids = set(manager.get_user_station_ids(user["id"]))
    if charge_point_id and station_ids and charge_point_id not in station_ids:
        return {"stats": []}

    stats = get_security_event_type_stats(
        start_date=start_dt,
        end_date=end_dt,
        charge_point_id=charge_point_id,
        charge_point_ids=None if charge_point_id else (list(station_ids) if station_ids else None),
    )
    return {"stats": stats}


@app.delete("/api/v1/security/clear")
async def clear_security_events(user=Depends(get_current_user)):
    security_monitor.clear_events()
    return {"status": "cleared"}


@app.get("/api/v1/security/flows")
async def get_security_flows(window_seconds: int = 60, user=Depends(get_current_user)):
    station_ids = set(manager.get_user_station_ids(user["id"]))
    snapshot = flow_tracker.get_counts_snapshot(window_seconds=window_seconds)
    by_station = snapshot.get("by_station", {})
    if not isinstance(by_station, dict):
        by_station = {}
    filtered = {
        "global": snapshot.get("global", {}),
        "by_station": {
            station_id: counts
            for station_id, counts in by_station.items()
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
    
    # NEW SIMULATIONS
    if action == "charge_manipulation":
        security_monitor.log_event(
            EventType.CHARGE_MANIPULATION_ATTACK,
            req.station_id,
            "Simulated Out-of-bounds SetChargingProfile request detected. Limits exceed 500A.",
            severity="critical"
        )
        return {"status": "ok", "message": "CMA attack simulated"}
        
    if action == "coordinated_attack":
        stations = ["PY-SIM-0001", "PY-SIM-0002", "PY-SIM-0003"]
        for s_id in stations:
            security_monitor.log_event(
                EventType.UNAUTHORIZED_ACTION,
                s_id,
                "Unauthorized configuration change attempt (Simulated)",
                severity="high"
            )
        return {"status": "ok", "message": "Botnet attack simulated"}
    if action == "inject_fault":
        raise HTTPException(status_code=400, detail="Replay-driven fault injection required")

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
            if req.target_message is None:
                raise HTTPException(status_code=400, detail="target_message required for tamper_payload")
            if req.duration is None:
                raise HTTPException(status_code=400, detail="duration required for tamper_payload")
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

    profile_name = manager.station_profiles.get(station_id)
    if not profile_name or profile_name not in manager.profiles:
        raise HTTPException(status_code=400, detail="Smart-charging requires an explicit profile")
    
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
    start_time: str = Query(..., description="Explicit ISO8601 start_time"),
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
        try:
            start_dt = datetime.fromisoformat(start_time)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid start_time format") from exc

        result = await chargepoint.request_composite_schedule_from_station(
            connector_id=connector_id,
            duration=duration,
            charging_rate_unit=charging_rate_unit,
            start_time=start_dt,
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
            off_peak_w_raw = req.off_peak_w
            peak_w_raw = req.peak_w
            peak_start_raw = req.peak_start_hour
            peak_end_raw = req.peak_end_hour
            if any(p is None for p in [off_peak_w_raw, peak_w_raw, peak_start_raw, peak_end_raw]):
                raise HTTPException(
                    status_code=400, 
                    detail="off_peak_w, peak_w, peak_start_hour, peak_end_hour required for time_of_use"
                )
            off_peak_w = float(cast(float, off_peak_w_raw))
            peak_w = float(cast(float, peak_w_raw))
            peak_start_hour = int(cast(int, peak_start_raw))
            peak_end_hour = int(cast(int, peak_end_raw))
            profile = create_time_of_use_profile(
                profile_id=2,
                off_peak_w=off_peak_w,
                peak_w=peak_w,
                peak_start_hour=peak_start_hour,
                peak_end_hour=peak_end_hour
            )
            
        elif req.scenario == "energy_cap":
            transaction_id_raw = req.transaction_id
            max_energy_wh_raw = req.max_energy_wh
            duration_seconds_raw = req.duration_seconds
            power_limit_w_raw = req.power_limit_w
            if any(p is None for p in [transaction_id_raw, max_energy_wh_raw, duration_seconds_raw, power_limit_w_raw]):
                raise HTTPException(
                    status_code=400, 
                    detail="transaction_id, max_energy_wh, duration_seconds, power_limit_w required for energy_cap"
                )
            transaction_id = int(cast(int, transaction_id_raw))
            max_energy_wh = float(cast(float, max_energy_wh_raw))
            duration_seconds = int(cast(int, duration_seconds_raw))
            power_limit_w = float(cast(float, power_limit_w_raw))
            profile = create_energy_cap_profile(
                profile_id=3,
                transaction_id=transaction_id,
                max_energy_wh=max_energy_wh,
                duration_seconds=duration_seconds,
                power_limit_w=power_limit_w
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
    try:
        created_at = datetime.fromisoformat(req.created_at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid created_at format") from exc
    user = create_user(req.email, api_key, created_at)
    return {"email": user["email"], "api_key": user["api_key"]}


def _render_page(request: Request, template_name: str, page_name: str, page_script: str) -> HTMLResponse:
    context = {
        "request": request,
        "page_name": page_name,
        "static_scope": f"/static/{page_name}",
        "page_script": page_script,
    }
    try:
        return templates.TemplateResponse(template_name, context)
    except TemplateNotFound:
        logger.warning("Template %s not found; falling back to index.html", template_name)
        fallback = dict(context)
        fallback["page_name"] = "index"
        fallback["static_scope"] = "/static"
        fallback["page_script"] = "app.js"
        return templates.TemplateResponse("index.html", fallback)


@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request):
    return _render_page(request, "landing.html", "landing", "landing.js")


@app.get("/operations", response_class=HTMLResponse)
async def operations_page(request: Request):
    return _render_page(request, "operations.html", "operations", "operations.js")


@app.get("/security", response_class=HTMLResponse)
async def security_page(request: Request):
    return _render_page(request, "security.html", "security", "security.js")
