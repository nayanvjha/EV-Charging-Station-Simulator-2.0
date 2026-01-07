# SmartCharging REST API - Quick Start Guide

## What Was Added

REST API endpoints for OCPP 1.6 Smart Charging in `controller_api.py`:

### 4 New Endpoints
1. **POST** `/stations/{station_id}/charging_profile` - Send charging profile
2. **GET** `/stations/{station_id}/composite_schedule` - Get composite schedule
3. **DELETE** `/stations/{station_id}/charging_profile` - Clear profiles
4. **POST** `/stations/{station_id}/test_profiles` - Generate & send test profiles

### 7 Pydantic Models
- `ChargingProfileRequest`, `CompositeScheduleRequest`, `ClearProfileRequest`, `TestProfileRequest`
- `ChargingProfileResponse`, `CompositeScheduleResponse`, `TestProfileResponse`

### Integration
- Imports CSMS helpers (`create_charge_point_max_profile`, `create_time_of_use_profile`, `create_energy_cap_profile`)
- Looks up station ChargePoint from `StationManager.station_chargepoints`
- Calls async CSMS methods (`send_charging_profile_to_station`, etc.)
- Comprehensive error handling and logging

---

## Quick Test (5 Minutes)

### Step 1: Start Services
```bash
# Terminal 1: Start CSMS
python csms_server.py

# Terminal 2: Start Controller API
uvicorn controller_api:app --reload

# Terminal 3: Start a station (via API)
curl -X POST http://localhost:8000/stations/start \
  -H "Content-Type: application/json" \
  -d '{"station_id": "PY-SIM-0001", "profile": "default"}'
```

### Step 2: Send a Test Profile
```bash
# Limit station to 11kW
curl -X POST http://localhost:8000/stations/PY-SIM-0001/test_profiles \
  -H "Content-Type: application/json" \
  -d '{
    "scenario": "peak_shaving",
    "connector_id": 1,
    "max_power_w": 11000
  }'
```

Expected response:
```json
{
  "status": "success",
  "station_id": "PY-SIM-0001",
  "scenario": "peak_shaving",
  "profile": {...},
  "send_status": "Accepted",
  "message": "Test profile generated and sent with status: Accepted"
}
```

### Step 3: Verify Profile is Active
```bash
# Get composite schedule
curl "http://localhost:8000/stations/PY-SIM-0001/composite_schedule?connector_id=1&duration=3600&charging_rate_unit=W"
```

### Step 4: Check Station Logs
```bash
# View recent logs
curl http://localhost:8000/stations/PY-SIM-0001/logs
```

Look for: `OCPP limit: 11000W → XWh`

### Step 5: Clear Profile
```bash
# Remove the profile
curl -X DELETE "http://localhost:8000/stations/PY-SIM-0001/charging_profile"
```

---

## Example Use Cases

### 1. Peak Shaving (11kW limit)
```bash
curl -X POST http://localhost:8000/stations/PY-SIM-0001/test_profiles \
  -H "Content-Type: application/json" \
  -d '{"scenario": "peak_shaving", "connector_id": 1, "max_power_w": 11000}'
```

### 2. Time-of-Use (Reduce power 9am-5pm)
```bash
curl -X POST http://localhost:8000/stations/PY-SIM-0001/test_profiles \
  -H "Content-Type: application/json" \
  -d '{
    "scenario": "time_of_use",
    "connector_id": 1,
    "off_peak_w": 22000,
    "peak_w": 7000,
    "peak_start_hour": 9,
    "peak_end_hour": 17
  }'
```

### 3. Energy Cap (30kWh max, 2 hours)
```bash
curl -X POST http://localhost:8000/stations/PY-SIM-0001/test_profiles \
  -H "Content-Type: application/json" \
  -d '{
    "scenario": "energy_cap",
    "connector_id": 1,
    "transaction_id": 1234,
    "max_energy_wh": 30000,
    "duration_seconds": 7200,
    "power_limit_w": 11000
  }'
```

### 4. Custom Profile
```bash
curl -X POST http://localhost:8000/stations/PY-SIM-0001/charging_profile \
  -H "Content-Type: application/json" \
  -d '{
    "connector_id": 1,
    "profile": {
      "chargingProfileId": 999,
      "stackLevel": 1,
      "chargingProfilePurpose": "TxDefaultProfile",
      "chargingProfileKind": "Absolute",
      "chargingSchedule": {
        "chargingRateUnit": "W",
        "chargingSchedulePeriod": [
          {"startPeriod": 0, "limit": 15000},
          {"startPeriod": 3600, "limit": 11000},
          {"startPeriod": 7200, "limit": 7000}
        ],
        "startSchedule": "2026-01-08T10:00:00+00:00"
      }
    }
  }'
```

---

## Test Suite

Run the comprehensive test suite:
```bash
python test_smartcharging_api.py
```

Tests include:
- Send custom profile
- Get composite schedule
- Clear specific profile
- Clear all profiles
- Peak shaving scenario
- Time-of-use scenario
- Energy cap scenario
- Error handling (station not found, invalid scenario, missing params)

---

## Interactive API Documentation

FastAPI provides automatic interactive documentation:

- **Swagger UI**: http://localhost:8000/docs
  - Try all endpoints directly in browser
  - See request/response schemas
  - Execute live requests

- **ReDoc**: http://localhost:8000/redoc
  - Clean, readable documentation
  - Example requests/responses

---

## Error Handling

### Station Not Found (404)
```bash
curl http://localhost:8000/stations/PY-SIM-9999/test_profiles \
  -H "Content-Type: application/json" \
  -d '{"scenario": "peak_shaving", "connector_id": 1, "max_power_w": 11000}'
```

Response:
```json
{
  "detail": "Station PY-SIM-9999 not found or not connected"
}
```

### Invalid Scenario (400)
```bash
curl -X POST http://localhost:8000/stations/PY-SIM-0001/test_profiles \
  -H "Content-Type: application/json" \
  -d '{"scenario": "invalid", "connector_id": 1}'
```

Response:
```json
{
  "detail": "Unknown scenario 'invalid'. Valid: peak_shaving, time_of_use, energy_cap"
}
```

### Missing Parameters (400)
```bash
curl -X POST http://localhost:8000/stations/PY-SIM-0001/test_profiles \
  -H "Content-Type: application/json" \
  -d '{"scenario": "peak_shaving", "connector_id": 1}'
```

Response:
```json
{
  "detail": "max_power_w is required for peak_shaving"
}
```

---

## Architecture Flow

```
HTTP Request
    ↓
FastAPI Endpoint (controller_api.py)
    ↓
Validate Request (Pydantic models)
    ↓
Lookup Station (StationManager.station_chargepoints)
    ↓
Call CSMS Method (ChargePoint.send_charging_profile_to_station)
    ↓
OCPP WebSocket (SetChargingProfile.req)
    ↓
Station Receives Profile (station.py)
    ↓
Profile Stored (ChargingProfileManager)
    ↓
Profile Enforced (MeterValues loop)
    ↓
Log Entry: "OCPP limit: 11000W → 3.06Wh"
```

---

## Key Files

### Modified
- `controller_api.py` - Added 4 endpoints, 7 Pydantic models, imports CSMS helpers

### Created
- `test_smartcharging_api.py` - Comprehensive test suite (10 tests)
- `SMARTCHARGING_REST_API.md` - Full API documentation
- `SMARTCHARGING_QUICKSTART.md` - This file

### Dependencies
- `csms_server.py` - Helper functions and async methods
- `station.py` - Profile enforcement in MeterValues loop
- `charging_profile_manager.py` - Profile storage and composite schedule

---

## Logging

All API operations are logged:

```
INFO:controller_api:API: Sending charging profile to PY-SIM-0001, connector 1
INFO:controller_api:Profile 100 accepted by PY-SIM-0001
INFO:controller_api:API: Requesting composite schedule from PY-SIM-0001, connector 1
INFO:controller_api:Composite schedule retrieved from PY-SIM-0001
INFO:controller_api:API: Clearing charging profiles from PY-SIM-0001
INFO:controller_api:Profiles cleared from PY-SIM-0001
INFO:controller_api:API: Generating test profile 'peak_shaving' for PY-SIM-0001
INFO:controller_api:Generated peak_shaving profile: 1
```

View logs in the terminal running the controller API.

---

## Next Steps

1. **Explore Interactive Docs**: http://localhost:8000/docs
2. **Run Test Suite**: `python test_smartcharging_api.py`
3. **Monitor Station Logs**: `GET /stations/{station_id}/logs`
4. **Test Different Scenarios**: peak_shaving, time_of_use, energy_cap
5. **Integrate with UI**: Add buttons to web interface for profile management
6. **Add Authentication**: Implement JWT/API keys for production
7. **Add Bulk Operations**: Send profiles to multiple stations

---

## Troubleshooting

### "Station not found"
- Ensure station is started: `POST /stations/start`
- Check station list: `GET /stations`
- Verify station_id matches exactly

### "Station does not support SmartCharging"
- Ensure station is using `CentralSystemChargePoint` class
- Check CSMS connection is active
- Verify OCPP 1.6 SmartCharging feature profile is enabled

### Profile not enforced
- Check station logs: `GET /stations/{station_id}/logs`
- Verify profile was accepted (check send_status)
- Ensure station has active transaction
- Check for OCPP log entries: "OCPP limit: XW → YWh"

### Connection errors
- Ensure CSMS server is running: `python csms_server.py`
- Check WebSocket connection on port 9000
- Verify station is connected to CSMS

---

## Full Documentation

See [SMARTCHARGING_REST_API.md](SMARTCHARGING_REST_API.md) for complete API reference.
