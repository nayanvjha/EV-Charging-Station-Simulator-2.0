# SmartCharging REST API Documentation

Complete REST API for OCPP 1.6 Smart Charging operations in controller_api.py.

## Overview

The SmartCharging REST API provides HTTP endpoints for managing charging profiles on EV charging stations. All operations are performed through the CSMS (Central System Management System) and support real-time profile management.

## Base URL

```
http://localhost:8000
```

## Authentication

Currently no authentication required (add JWT/API keys in production).

---

## Endpoints

### 1. Send Charging Profile

Send a custom charging profile to a specific station.

**Endpoint:** `POST /stations/{station_id}/charging_profile`

**Path Parameters:**
- `station_id` (string, required): Station identifier (e.g., "PY-SIM-0001")

**Request Body:**
```json
{
  "connector_id": 1,
  "profile": {
    "chargingProfileId": 100,
    "stackLevel": 0,
    "chargingProfilePurpose": "ChargePointMaxProfile",
    "chargingProfileKind": "Absolute",
    "chargingSchedule": {
      "chargingRateUnit": "W",
      "chargingSchedulePeriod": [
        {"startPeriod": 0, "limit": 22000}
      ],
      "startSchedule": "2026-01-08T10:00:00+00:00"
    }
  }
}
```

**Response:**
```json
{
  "status": "success",
  "station_id": "PY-SIM-0001",
  "connector_id": 1,
  "profile_id": 100,
  "message": "Charging profile 100 sent successfully",
  "error": null
}
```

**Status Codes:**
- `200`: Profile sent successfully (check `status` field for "success" or "rejected")
- `404`: Station not found
- `400`: Station doesn't support SmartCharging

**curl Example:**
```bash
curl -X POST http://localhost:8000/stations/PY-SIM-0001/charging_profile \
  -H "Content-Type: application/json" \
  -d '{
    "connector_id": 1,
    "profile": {
      "chargingProfileId": 100,
      "stackLevel": 0,
      "chargingProfilePurpose": "ChargePointMaxProfile",
      "chargingProfileKind": "Absolute",
      "chargingSchedule": {
        "chargingRateUnit": "W",
        "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 22000}]
      }
    }
  }'
```

---

### 2. Get Composite Schedule

Request the current composite charging schedule from a station.

**Endpoint:** `GET /stations/{station_id}/composite_schedule`

**Path Parameters:**
- `station_id` (string, required): Station identifier

**Query Parameters:**
- `connector_id` (integer, required): Connector ID
- `duration` (integer, required): Duration in seconds
- `charging_rate_unit` (string, optional): "W" (Watts) or "A" (Amps), default: "W"

**Response:**
```json
{
  "status": "success",
  "station_id": "PY-SIM-0001",
  "connector_id": 1,
  "schedule": {
    "chargingRateUnit": "W",
    "chargingSchedulePeriod": [
      {"startPeriod": 0, "limit": 22000}
    ],
    "duration": 3600
  },
  "message": "Composite schedule retrieved successfully",
  "error": null
}
```

**Status Codes:**
- `200`: Request processed (check `status` field)
- `404`: Station not found
- `400`: Station doesn't support SmartCharging

**curl Example:**
```bash
curl -X GET "http://localhost:8000/stations/PY-SIM-0001/composite_schedule?connector_id=1&duration=3600&charging_rate_unit=W"
```

---

### 3. Clear Charging Profiles

Remove charging profiles from a station using optional filters.

**Endpoint:** `DELETE /stations/{station_id}/charging_profile`

**Path Parameters:**
- `station_id` (string, required): Station identifier

**Query Parameters (all optional, filters are combined with AND logic):**
- `profile_id` (integer): Clear specific profile by ID
- `connector_id` (integer): Clear profiles for specific connector
- `purpose` (string): Clear profiles with specific purpose
- `stack_level` (integer): Clear profiles at specific stack level

**Response:**
```json
{
  "status": "success",
  "station_id": "PY-SIM-0001",
  "message": "Charging profiles cleared successfully",
  "filters": {
    "profile_id": 100,
    "connector_id": 1,
    "purpose": null,
    "stack_level": null
  }
}
```

**Status Codes:**
- `200`: Request processed (check `status` field)
- `404`: Station not found
- `400`: Station doesn't support SmartCharging

**curl Examples:**

Clear specific profile:
```bash
curl -X DELETE "http://localhost:8000/stations/PY-SIM-0001/charging_profile?profile_id=100"
```

Clear all profiles on connector 1:
```bash
curl -X DELETE "http://localhost:8000/stations/PY-SIM-0001/charging_profile?connector_id=1"
```

Clear all profiles:
```bash
curl -X DELETE "http://localhost:8000/stations/PY-SIM-0001/charging_profile"
```

---

### 4. Send Test Profile

Generate and send a test charging profile based on common scenarios.

**Endpoint:** `POST /stations/{station_id}/test_profiles`

**Path Parameters:**
- `station_id` (string, required): Station identifier

**Request Body:**

**Scenario 1: Peak Shaving** (Limit station max power)
```json
{
  "scenario": "peak_shaving",
  "connector_id": 1,
  "max_power_w": 11000
}
```

**Scenario 2: Time of Use** (Daily recurring with peak/off-peak)
```json
{
  "scenario": "time_of_use",
  "connector_id": 1,
  "off_peak_w": 11000,
  "peak_w": 7000,
  "peak_start_hour": 9,
  "peak_end_hour": 17
}
```

**Scenario 3: Energy Cap** (Transaction-specific limit)
```json
{
  "scenario": "energy_cap",
  "connector_id": 1,
  "transaction_id": 1234,
  "max_energy_wh": 30000,
  "duration_seconds": 7200,
  "power_limit_w": 11000
}
```

**Response:**
```json
{
  "status": "success",
  "station_id": "PY-SIM-0001",
  "scenario": "peak_shaving",
  "profile": {
    "chargingProfileId": 1,
    "stackLevel": 0,
    "chargingProfilePurpose": "ChargePointMaxProfile",
    "chargingProfileKind": "Absolute",
    "chargingSchedule": {
      "chargingRateUnit": "W",
      "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 11000}],
      "startSchedule": "2026-01-08T10:30:00+00:00"
    }
  },
  "send_status": "Accepted",
  "message": "Test profile generated and sent with status: Accepted",
  "error": null
}
```

**Status Codes:**
- `200`: Profile generated and sent (check `send_status` field)
- `404`: Station not found
- `400`: Invalid scenario or missing required parameters

**curl Examples:**

Peak shaving:
```bash
curl -X POST http://localhost:8000/stations/PY-SIM-0001/test_profiles \
  -H "Content-Type: application/json" \
  -d '{"scenario": "peak_shaving", "connector_id": 1, "max_power_w": 11000}'
```

Time of use:
```bash
curl -X POST http://localhost:8000/stations/PY-SIM-0001/test_profiles \
  -H "Content-Type: application/json" \
  -d '{
    "scenario": "time_of_use",
    "connector_id": 1,
    "off_peak_w": 11000,
    "peak_w": 7000,
    "peak_start_hour": 9,
    "peak_end_hour": 17
  }'
```

Energy cap:
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

---

## Pydantic Models

### Request Models

#### ChargingProfileRequest
```python
class ChargingProfileRequest(BaseModel):
    connector_id: int
    profile: dict
```

#### CompositeScheduleRequest
```python
class CompositeScheduleRequest(BaseModel):
    connector_id: int
    duration: int
    charging_rate_unit: str = "W"
```

#### ClearProfileRequest
```python
class ClearProfileRequest(BaseModel):
    profile_id: Optional[int] = None
    connector_id: Optional[int] = None
    purpose: Optional[str] = None
    stack_level: Optional[int] = None
```

#### TestProfileRequest
```python
class TestProfileRequest(BaseModel):
    scenario: str  # "peak_shaving", "time_of_use", "energy_cap"
    connector_id: int = 1
    # Scenario-specific fields...
```

### Response Models

#### ChargingProfileResponse
```python
class ChargingProfileResponse(BaseModel):
    status: str
    station_id: str
    connector_id: int
    profile_id: Optional[int] = None
    message: str
    error: Optional[str] = None
```

#### CompositeScheduleResponse
```python
class CompositeScheduleResponse(BaseModel):
    status: str
    station_id: str
    connector_id: int
    schedule: Optional[dict] = None
    message: str
    error: Optional[str] = None
```

#### TestProfileResponse
```python
class TestProfileResponse(BaseModel):
    status: str
    station_id: str
    scenario: str
    profile: dict
    send_status: str
    message: str
    error: Optional[str] = None
```

---

## Error Handling

All endpoints handle the following error cases:

### Station Not Found (404)
```json
{
  "detail": "Station PY-SIM-9999 not found or not connected"
}
```

### Station Doesn't Support SmartCharging (400)
```json
{
  "detail": "Station PY-SIM-0001 does not support SmartCharging (CentralSystemChargePoint required)"
}
```

### Invalid Scenario (400)
```json
{
  "detail": "Unknown scenario 'invalid_scenario'. Valid: peak_shaving, time_of_use, energy_cap"
}
```

### Missing Required Parameters (400)
```json
{
  "detail": "max_power_w is required for peak_shaving"
}
```

### OCPP Error (200 with error status)
```json
{
  "status": "error",
  "message": "Failed to send charging profile",
  "error": "Connection timeout"
}
```

---

## Testing

### Prerequisites
1. Start CSMS server: `python csms_server.py`
2. Start controller API: `uvicorn controller_api:app --reload`
3. Start at least one station via API or UI

### Run Test Suite
```bash
python test_smartcharging_api.py
```

### Interactive API Documentation
FastAPI provides automatic interactive documentation:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

---

## Logging

All API calls are logged with INFO level:

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

---

## Integration with Station Logs

After sending profiles, check station logs to verify enforcement:

```bash
curl http://localhost:8000/stations/PY-SIM-0001/logs
```

Look for log entries like:
```
OCPP limit: 11000W → 3.06Wh (sample_interval=1.0s)
```

---

## Use Cases

### 1. Load Balancing
Dynamically adjust station power limits based on grid capacity:
```bash
# Reduce to 7kW during peak
curl -X POST http://localhost:8000/stations/PY-SIM-0001/test_profiles \
  -H "Content-Type: application/json" \
  -d '{"scenario": "peak_shaving", "connector_id": 1, "max_power_w": 7000}'

# Restore to 22kW off-peak
curl -X DELETE "http://localhost:8000/stations/PY-SIM-0001/charging_profile"
```

### 2. Time-of-Use Optimization
Automatically reduce charging rates during expensive hours:
```bash
curl -X POST http://localhost:8000/stations/PY-SIM-0001/test_profiles \
  -H "Content-Type: application/json" \
  -d '{
    "scenario": "time_of_use",
    "connector_id": 1,
    "off_peak_w": 22000,
    "peak_w": 11000,
    "peak_start_hour": 8,
    "peak_end_hour": 18
  }'
```

### 3. Fleet Charging Management
Cap energy per session for fleet vehicles:
```bash
curl -X POST http://localhost:8000/stations/PY-SIM-0001/test_profiles \
  -H "Content-Type: application/json" \
  -d '{
    "scenario": "energy_cap",
    "connector_id": 1,
    "transaction_id": 1234,
    "max_energy_wh": 40000,
    "duration_seconds": 14400,
    "power_limit_w": 11000
  }'
```

### 4. Emergency Load Shedding
Immediately reduce all stations to minimum power:
```bash
# Send to all active stations
for station in $(curl -s http://localhost:8000/stations | jq -r '.[].station_id'); do
  curl -X POST http://localhost:8000/stations/$station/test_profiles \
    -H "Content-Type: application/json" \
    -d '{"scenario": "peak_shaving", "connector_id": 1, "max_power_w": 3000}'
done
```

---

## Architecture

```
┌─────────────────┐
│  REST Client    │
│  (curl/webapp)  │
└────────┬────────┘
         │ HTTP
         ▼
┌─────────────────┐
│ controller_api  │ FastAPI
│  (Port 8000)    │ - Validates requests
└────────┬────────┘ - Generates profiles
         │          - Logs operations
         ▼
┌─────────────────┐
│ StationManager  │
│  .station_      │
│   chargepoints  │ ChargePoint instances
└────────┬────────┘
         │ OCPP async methods
         ▼
┌─────────────────┐
│ CentralSystem   │
│  ChargePoint    │ CSMS ChargePoint class
└────────┬────────┘ - send_charging_profile_to_station()
         │          - request_composite_schedule_from_station()
         │          - clear_charging_profile_from_station()
         ▼
┌─────────────────┐
│  OCPP WebSocket │
│  (Port 9000)    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  EV Station     │
│  (station.py)   │ - Receives profiles
└─────────────────┘ - Enforces limits
                    - Reports schedules
```

---

## Next Steps

1. **Add Authentication**: Implement JWT or API key authentication for production
2. **Add Rate Limiting**: Prevent API abuse
3. **Add Profile Validation**: Validate profile structure before sending
4. **Add Bulk Operations**: Send profiles to multiple stations at once
5. **Add Schedule Preview**: Calculate expected charge time/cost before applying
6. **Add Profile History**: Track profile changes over time
7. **Add WebSocket Notifications**: Real-time updates when profiles are applied/rejected

---

## Related Documentation

- [CSMS SmartCharging Helpers](CSMS_SMARTCHARGING_HELPERS.md)
- [OCPP SmartCharging Integration](OCPP_SMARTCHARGING_INTEGRATION.md)
- [Station Logging System](STATION_LOGGING_SYSTEM.md)

---

## Support

For issues or questions:
1. Check station logs: `GET /stations/{station_id}/logs`
2. Check CSMS logs for OCPP message details
3. Verify station supports SmartCharging (CentralSystemChargePoint)
4. Test with interactive docs: http://localhost:8000/docs
