# SmartCharging User Guide

**Complete guide to OCPP 1.6 Smart Charging for EV charging station management**

Version: 1.0  
Date: January 8, 2026

---

## Table of Contents

1. [Overview](#overview)
2. [SmartCharging vs Legacy Policy](#smartcharging-vs-legacy-policy)
3. [Quick Start](#quick-start)
4. [Dashboard UI Guide](#dashboard-ui-guide)
5. [REST API Reference](#rest-api-reference)
6. [Profile Examples](#profile-examples)
7. [Troubleshooting](#troubleshooting)
8. [Best Practices](#best-practices)
9. [Advanced Topics](#advanced-topics)

---

## Overview

### What is OCPP SmartCharging?

OCPP (Open Charge Point Protocol) SmartCharging is an industry-standard protocol feature that enables remote control of charging station power limits through **Charging Profiles**. This allows dynamic management of charging rates based on:

- Grid capacity and demand
- Time-of-use electricity pricing
- Energy budgets and caps
- Load balancing across multiple stations
- Renewable energy availability

### Key Benefits

✅ **Dynamic Power Management** - Adjust charging rates in real-time  
✅ **Cost Optimization** - Reduce charging during peak rate hours  
✅ **Grid Integration** - Respond to grid signals and constraints  
✅ **Energy Budgets** - Cap energy per session  
✅ **Standards-Based** - OCPP 1.6-compliant  
✅ **Remote Control** - Manage stations from central dashboard

---

## SmartCharging vs Legacy Policy

This system supports **two parallel control mechanisms**:

### Legacy Policy Engine (Pre-existing)

**How it works:**
- Station-local Python logic evaluates price and policy rules
- Runs continuously during charging sessions
- Can start/stop charging based on conditions
- No OCPP messages required

**Control signals:**
```
✓ Policy: OK        → Charging allowed, no restrictions
🔒 Policy: Blocked  → Charging stopped by policy engine
```

**Use cases:**
- Price-based charging control
- Simple time-of-day restrictions
- Standalone operation without CSMS

---

### OCPP SmartCharging (New)

**How it works:**
- CSMS sends charging profiles to station via OCPP
- Station enforces power limits in real-time
- Profiles can be time-based, recurring, or transaction-specific
- Overrides legacy policy when active

**Control signals:**
```
⚡ OCPP: 7.4 kW     → SmartCharging profile active (power limit)
```

**Use cases:**
- Dynamic load management
- Peak shaving
- Time-of-use optimization
- Energy cap per session
- Grid-responsive charging

---

### Priority Hierarchy

When both systems are present:

```
┌─────────────────────────────────────────────┐
│  1. OCPP SmartCharging (if profiles exist)  │  ← Highest Priority
├─────────────────────────────────────────────┤
│  2. Legacy Policy Engine                    │
├─────────────────────────────────────────────┤
│  3. Station Hardware Limits                 │  ← Lowest Priority
└─────────────────────────────────────────────┘
```

**Key principle:** OCPP profiles take precedence over legacy policy when active. Clear all profiles to restore legacy behavior.

---

## Quick Start

### 5-Minute Quickstart

#### Option 1: Dashboard UI (Easiest)

1. **Start the services:**
   ```bash
   # Terminal 1: Start CSMS
   python csms_server.py
   
   # Terminal 2: Start Controller API
   uvicorn controller_api:app --reload
   ```

2. **Open dashboard:**
   ```
   http://localhost:8000
   ```

3. **Create and start stations:**
   - Click "Scale to 3 Stations"
   - Wait for stations to start

4. **Send test profile:**
   - Select station from SmartCharging dropdown
   - Click **🔋 7.4kW** button
   - Watch success toast appear

5. **Verify OCPP control:**
   - Wait 5 seconds for status update
   - See green badge: **⚡ OCPP: 7.4 kW**
   - Monitor Usage column (stays ≤7.4 kW)

6. **View profile details:**
   - Click **📋 View Profiles** button
   - Review active profiles and timeline
   - See composite schedule visualization

#### Option 2: REST API (Programmatic)

```bash
# 1. Send 7.4kW peak shaving profile
curl -X POST http://localhost:8000/stations/PY-SIM-0001/test_profiles \
  -H "Content-Type: application/json" \
  -d '{
    "scenario": "peak_shaving",
    "connector_id": 1,
    "max_power_w": 7400
  }'

# 2. Get composite schedule
curl http://localhost:8000/stations/PY-SIM-0001/composite_schedule?connector_id=1&duration=3600

# 3. Clear all profiles
curl -X DELETE http://localhost:8000/stations/PY-SIM-0001/charging_profile
```

---

## Dashboard UI Guide

### SmartCharging Panel

Located at the top of the dashboard:

```
┌─────────────────────────────────────────────────────────┐
│  ⚡ SmartCharging Profiles                              │
│                                                           │
│  [Select a station...        ▼]                          │
│                                                           │
│  [🔋 7.4kW]  [⏰ TOU]  [📊 30kWh]                        │
│                                                           │
│  [📋 View Profiles]  [🗑️ Clear All]                     │
└─────────────────────────────────────────────────────────┘
```

#### Controls

**Station Selector**
- Dropdown lists all running stations
- Auto-updates as stations start/stop

**Test Profile Buttons**
- **🔋 7.4kW** - Send peak shaving profile (7.4kW limit)
- **⏰ TOU** - Send time-of-use profile (22kW off-peak, 7kW peak 18:00-22:00)
- **📊 30kWh** - Send energy cap profile (30kWh limit per session)

**Management Buttons**
- **📋 View Profiles** - Open profile viewer modal
- **🗑️ Clear All** - Remove all charging profiles

---

### OCPP Status Column

New column in station table showing control status:

#### Status Indicators

**⚡ OCPP: 7.4 kW** (Green)
- SmartCharging profile active
- Shows current power limit
- Displays profile count in parentheses
- Row highlighted with green border

**✓ Policy: OK** (Blue)
- Legacy policy engine active
- No OCPP profiles
- Normal charging allowed

**🔒 Policy: Blocked** (Red)
- Legacy policy blocking charging
- Price too high or outside allowed hours
- No OCPP profiles active

---

### Profile Viewer Modal

Click **View Profiles** to open detailed modal:

```
╔═══════════════════════════════════════════════════════════╗
║  Profile Viewer - PY-SIM-0001 / Connector 1              ║
╠═══════════════════════════════════════════════════════════╣
║  Active Profiles (2)                                      ║
║                                                           ║
║  Profile #1 (ChargePointMaxProfile)                      ║
║    Purpose: ChargePointMaxProfile                        ║
║    Kind: Absolute                                         ║
║    Stack Level: 0                                         ║
║                                                           ║
║  Profile #2 (TxDefaultProfile)                           ║
║    Purpose: TxDefaultProfile                             ║
║    Kind: Recurring (Daily)                                ║
║    Stack Level: 1                                         ║
║                                                           ║
╟───────────────────────────────────────────────────────────╢
║  Composite Schedule (Next 60 minutes)                    ║
║                                                           ║
║  12:00-13:00  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓ 7.4 kW                     ║
║  13:00-14:00  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓ 7.4 kW                     ║
║  14:00-15:00  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓ 7.4 kW                     ║
║                                                           ║
╟───────────────────────────────────────────────────────────╢
║  [Refresh]  [Clear All Profiles]  [Close]                ║
╚═══════════════════════════════════════════════════════════╝
```

**Features:**
- Lists all active profiles with details
- Shows composite schedule timeline
- Visual power limit bars
- Real-time refresh capability

---

### Auto-Polling

Dashboard automatically updates OCPP status every 5 seconds:

**What it does:**
1. Fetches station logs
2. Parses OCPP limit messages
3. Counts active profiles
4. Updates status badges
5. Highlights OCPP-controlled rows

**No manual refresh needed!**

---

## REST API Reference

### Base URL

```
http://localhost:8000
```

All endpoints require station to be connected to CSMS.

---

### 1. Send Charging Profile

Send a charging profile to a station.

**Endpoint:**
```
POST /stations/{station_id}/charging_profile
```

**Request Body:**
```json
{
  "connector_id": 1,
  "profile": {
    "chargingProfileId": 1,
    "stackLevel": 0,
    "chargingProfilePurpose": "ChargePointMaxProfile",
    "chargingProfileKind": "Absolute",
    "chargingSchedule": {
      "chargingRateUnit": "W",
      "chargingSchedulePeriod": [
        {
          "startPeriod": 0,
          "limit": 7400
        }
      ]
    }
  }
}
```

**Response:**
```json
{
  "status": "success",
  "station_id": "PY-SIM-0001",
  "profile_id": 1,
  "csms_status": "Accepted",
  "message": "Profile sent successfully"
}
```

**Error Responses:**

```json
// 404 - Station not found
{
  "detail": "Station PY-SIM-9999 not found or not connected"
}

// 500 - CSMS error
{
  "detail": "Failed to send profile: Connection timeout"
}
```

**Example (curl):**
```bash
curl -X POST http://localhost:8000/stations/PY-SIM-0001/charging_profile \
  -H "Content-Type: application/json" \
  -d '{
    "connector_id": 1,
    "profile": {
      "chargingProfileId": 1,
      "stackLevel": 0,
      "chargingProfilePurpose": "ChargePointMaxProfile",
      "chargingProfileKind": "Absolute",
      "chargingSchedule": {
        "chargingRateUnit": "W",
        "chargingSchedulePeriod": [
          {"startPeriod": 0, "limit": 7400}
        ]
      }
    }
  }'
```

---

### 2. Get Composite Schedule

Request the composite schedule (merged result of all active profiles).

**Endpoint:**
```
GET /stations/{station_id}/composite_schedule
```

**Query Parameters:**
- `connector_id` (required) - Connector number (1-based)
- `duration` (required) - Duration in seconds (e.g., 3600 = 1 hour)
- `charging_rate_unit` (optional) - "W" or "A" (default: "W")

**Response:**
```json
{
  "status": "success",
  "station_id": "PY-SIM-0001",
  "connector_id": 1,
  "schedule": {
    "duration": 3600,
    "startSchedule": "2026-01-08T12:00:00+00:00",
    "chargingRateUnit": "W",
    "chargingSchedulePeriod": [
      {
        "startPeriod": 0,
        "limit": 7400
      },
      {
        "startPeriod": 3600,
        "limit": 22000
      }
    ]
  }
}
```

**Example (curl):**
```bash
# Get next 1 hour schedule
curl "http://localhost:8000/stations/PY-SIM-0001/composite_schedule?connector_id=1&duration=3600&charging_rate_unit=W"

# Get next 24 hours
curl "http://localhost:8000/stations/PY-SIM-0001/composite_schedule?connector_id=1&duration=86400"
```

---

### 3. Clear Charging Profile

Remove charging profiles from a station.

**Endpoint:**
```
DELETE /stations/{station_id}/charging_profile
```

**Query Parameters (all optional):**
- `profile_id` - Clear specific profile by ID
- `connector_id` - Clear profiles on specific connector
- `charging_profile_purpose` - Clear by purpose ("ChargePointMaxProfile", "TxDefaultProfile", "TxProfile")
- `stack_level` - Clear profiles at specific stack level

**Response:**
```json
{
  "status": "success",
  "station_id": "PY-SIM-0001",
  "csms_status": "Accepted",
  "message": "Profiles cleared successfully",
  "filters": {
    "profile_id": 1
  }
}
```

**Examples (curl):**
```bash
# Clear all profiles
curl -X DELETE http://localhost:8000/stations/PY-SIM-0001/charging_profile

# Clear specific profile
curl -X DELETE "http://localhost:8000/stations/PY-SIM-0001/charging_profile?profile_id=1"

# Clear all ChargePointMaxProfiles
curl -X DELETE "http://localhost:8000/stations/PY-SIM-0001/charging_profile?charging_profile_purpose=ChargePointMaxProfile"

# Clear profiles on connector 1
curl -X DELETE "http://localhost:8000/stations/PY-SIM-0001/charging_profile?connector_id=1"
```

---

### 4. Send Test Profile

Send pre-configured test profiles for common scenarios.

**Endpoint:**
```
POST /stations/{station_id}/test_profiles
```

**Request Body (Peak Shaving):**
```json
{
  "scenario": "peak_shaving",
  "connector_id": 1,
  "max_power_w": 7400
}
```

**Request Body (Time-of-Use):**
```json
{
  "scenario": "time_of_use",
  "connector_id": 1,
  "off_peak_w": 22000,
  "peak_w": 7000,
  "peak_start_hour": 18,
  "peak_end_hour": 22
}
```

**Request Body (Energy Cap):**
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
    "chargingProfilePurpose": "ChargePointMaxProfile",
    "chargingProfileKind": "Absolute",
    "stackLevel": 0,
    "chargingSchedule": {
      "chargingRateUnit": "W",
      "chargingSchedulePeriod": [
        {"startPeriod": 0, "limit": 7400}
      ]
    }
  },
  "csms_status": "Accepted"
}
```

**Error Response:**
```json
// 400 - Invalid scenario or missing parameters
{
  "detail": "Unknown scenario: invalid_scenario. Must be one of: peak_shaving, time_of_use, energy_cap"
}

{
  "detail": "Missing required parameter 'max_power_w' for peak_shaving scenario"
}
```

**Examples (curl):**
```bash
# Peak shaving (7.4kW)
curl -X POST http://localhost:8000/stations/PY-SIM-0001/test_profiles \
  -H "Content-Type: application/json" \
  -d '{
    "scenario": "peak_shaving",
    "connector_id": 1,
    "max_power_w": 7400
  }'

# Time-of-use (22kW off-peak, 7kW peak 18:00-22:00)
curl -X POST http://localhost:8000/stations/PY-SIM-0001/test_profiles \
  -H "Content-Type: application/json" \
  -d '{
    "scenario": "time_of_use",
    "connector_id": 1,
    "off_peak_w": 22000,
    "peak_w": 7000,
    "peak_start_hour": 18,
    "peak_end_hour": 22
  }'

# Energy cap (30kWh session limit)
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

## Profile Examples

### Example 1: Peak Shaving (7.4kW Limit)

**Use case:** Limit station to 7.4kW (32A @ 230V) to avoid overloading circuit.

**Profile:**
```json
{
  "chargingProfileId": 1,
  "stackLevel": 0,
  "chargingProfilePurpose": "ChargePointMaxProfile",
  "chargingProfileKind": "Absolute",
  "chargingSchedule": {
    "chargingRateUnit": "W",
    "chargingSchedulePeriod": [
      {
        "startPeriod": 0,
        "limit": 7400
      }
    ]
  }
}
```

**Behavior:**
- Applies to all connectors (sent to connector 0)
- Continuous limit (no time restrictions)
- Overrides station's default 22kW capability
- Station will not charge above 7.4kW

**Dashboard shortcut:** Click **🔋 7.4kW** button

**API call:**
```bash
curl -X POST http://localhost:8000/stations/PY-SIM-0001/test_profiles \
  -H "Content-Type: application/json" \
  -d '{"scenario": "peak_shaving", "connector_id": 1, "max_power_w": 7400}'
```

---

### Example 2: Time-of-Use Pricing

**Use case:** Reduce charging rate during peak electricity pricing hours (18:00-22:00).

**Profile:**
```json
{
  "chargingProfileId": 2,
  "stackLevel": 0,
  "chargingProfilePurpose": "TxDefaultProfile",
  "chargingProfileKind": "Recurring",
  "recurrencyKind": "Daily",
  "chargingSchedule": {
    "chargingRateUnit": "W",
    "chargingSchedulePeriod": [
      {
        "startPeriod": 0,
        "limit": 22000,
        "numberPhases": 3
      },
      {
        "startPeriod": 64800,
        "limit": 7000,
        "numberPhases": 3
      },
      {
        "startPeriod": 79200,
        "limit": 22000,
        "numberPhases": 3
      }
    ]
  }
}
```

**Schedule breakdown:**
- `00:00-18:00` (startPeriod 0) → 22kW off-peak rate
- `18:00-22:00` (startPeriod 64800 = 18*3600) → 7kW peak rate
- `22:00-24:00` (startPeriod 79200 = 22*3600) → 22kW off-peak rate

**Behavior:**
- Repeats every day (Recurring + Daily)
- Automatically adjusts at 18:00 and 22:00
- No manual intervention needed
- Optimizes cost while maintaining service

**Dashboard shortcut:** Click **⏰ TOU** button

**API call:**
```bash
curl -X POST http://localhost:8000/stations/PY-SIM-0001/test_profiles \
  -H "Content-Type: application/json" \
  -d '{
    "scenario": "time_of_use",
    "connector_id": 1,
    "off_peak_w": 22000,
    "peak_w": 7000,
    "peak_start_hour": 18,
    "peak_end_hour": 22
  }'
```

---

### Example 3: Energy Cap (30kWh Session Limit)

**Use case:** Cap charging session at 30kWh (e.g., for prepaid customers or demos).

**Profile:**
```json
{
  "chargingProfileId": 3,
  "transactionId": 1234,
  "stackLevel": 0,
  "chargingProfilePurpose": "TxProfile",
  "chargingProfileKind": "Absolute",
  "chargingSchedule": {
    "duration": 7200,
    "chargingRateUnit": "W",
    "chargingSchedulePeriod": [
      {
        "startPeriod": 0,
        "limit": 11000
      }
    ]
  }
}
```

**Behavior:**
- Only applies to transaction 1234
- Limits to 11kW for 2 hours (7200s)
- Maximum energy: 11kW * 2h = 22kWh
- Station stops charging when energy cap reached
- Other transactions not affected

**Dashboard shortcut:** Click **📊 30kWh** button

**API call:**
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

### Example 4: Multiple Stacked Profiles

**Use case:** Combine station-wide limit + transaction-specific limit.

**Profile 1 - Station-Wide Limit (ChargePointMax):**
```json
{
  "chargingProfileId": 1,
  "stackLevel": 0,
  "chargingProfilePurpose": "ChargePointMaxProfile",
  "chargingProfileKind": "Absolute",
  "chargingSchedule": {
    "chargingRateUnit": "W",
    "chargingSchedulePeriod": [
      {"startPeriod": 0, "limit": 22000}
    ]
  }
}
```

**Profile 2 - Transaction Limit (TxProfile):**
```json
{
  "chargingProfileId": 2,
  "transactionId": 1234,
  "stackLevel": 0,
  "chargingProfilePurpose": "TxProfile",
  "chargingProfileKind": "Absolute",
  "chargingSchedule": {
    "chargingRateUnit": "W",
    "chargingSchedulePeriod": [
      {"startPeriod": 0, "limit": 7400}
    ]
  }
}
```

**Priority resolution:**
```
Station-wide:  22kW (ChargePointMaxProfile)
Transaction:    7.4kW (TxProfile - higher priority)
Result:         7.4kW (most restrictive wins)
```

**Send both profiles:**
```bash
# 1. Send station-wide 22kW limit
curl -X POST http://localhost:8000/stations/PY-SIM-0001/charging_profile \
  -H "Content-Type: application/json" \
  -d '{"connector_id": 0, "profile": {...}}'

# 2. Send transaction-specific 7.4kW limit
curl -X POST http://localhost:8000/stations/PY-SIM-0001/charging_profile \
  -H "Content-Type: application/json" \
  -d '{"connector_id": 1, "profile": {...}}'
```

---

### Example 5: Profile with Expiration

**Use case:** Temporary limit that expires after 1 hour.

**Profile:**
```json
{
  "chargingProfileId": 4,
  "stackLevel": 0,
  "chargingProfilePurpose": "ChargePointMaxProfile",
  "chargingProfileKind": "Absolute",
  "validFrom": "2026-01-08T12:00:00+00:00",
  "validTo": "2026-01-08T13:00:00+00:00",
  "chargingSchedule": {
    "chargingRateUnit": "W",
    "chargingSchedulePeriod": [
      {"startPeriod": 0, "limit": 5000}
    ]
  }
}
```

**Behavior:**
- Active from 12:00 to 13:00
- Limits to 5kW during that hour
- Automatically expires at 13:00
- Station reverts to next profile or legacy policy

**Timeline:**
```
11:59 → No profile (full power)
12:00 → Profile activates (5kW limit)
12:30 → Still active (5kW limit)
13:00 → Profile expires (full power restored)
```

---

## Troubleshooting

### Problem: Profile Not Applied

**Symptoms:**
- Station still charging at full power
- OCPP status shows "Policy: OK" (blue)
- No green OCPP badge appears

**Possible causes:**

1. **Profile rejected by station**
   ```
   Check logs: "Profile rejected: InvalidValue"
   ```
   **Solution:** Verify profile structure matches OCPP 1.6 spec

2. **Profile expired (validTo in past)**
   ```json
   "validTo": "2026-01-07T12:00:00+00:00"  // Already expired!
   ```
   **Solution:** Remove `validTo` or set future timestamp

3. **Station not connected to CSMS**
   ```
   Error: Station PY-SIM-0001 not found or not connected
   ```
   **Solution:** Verify station is running and connected

4. **Wrong connector ID**
   ```json
   {"connector_id": 5}  // Station only has 1 connector!
   ```
   **Solution:** Use connector_id 0 for station-wide or 1 for first connector

5. **Empty charging schedule**
   ```json
   "chargingSchedulePeriod": []  // No periods!
   ```
   **Solution:** Add at least one period with startPeriod 0

---

### Problem: Wrong Limit Applied

**Symptoms:**
- Station enforces different limit than expected
- Composite schedule shows unexpected values

**Possible causes:**

1. **stackLevel conflict**
   ```json
   // Profile A
   {"stackLevel": 0, "limit": 7400}
   
   // Profile B (higher priority!)
   {"stackLevel": 0, "limit": 11000}
   ```
   **Issue:** Both have same stackLevel (0), station picks one arbitrarily
   
   **Solution:** Use different stackLevels:
   ```json
   {"stackLevel": 0, "limit": 7400}   // Higher priority
   {"stackLevel": 1, "limit": 11000}  // Lower priority
   ```

2. **Purpose priority misunderstanding**
   ```
   TxProfile (7.4kW)           → Highest priority
   TxDefaultProfile (11kW)     → Medium priority
   ChargePointMaxProfile (22kW) → Lowest priority
   ```
   **Solution:** Understand that TxProfile always wins for that transaction

3. **Time-based schedule not active yet**
   ```json
   "chargingSchedulePeriod": [
     {"startPeriod": 0, "limit": 22000},
     {"startPeriod": 3600, "limit": 7000}  // 1 hour from start
   ]
   ```
   **Solution:** Check current time offset from schedule start

---

### Problem: Invalid Schedule Times

**Symptoms:**
- Profile rejected with error
- Schedule periods not applied correctly

**Common errors:**

1. **Negative startPeriod**
   ```json
   {"startPeriod": -3600, "limit": 7400}  // ❌ Invalid!
   ```
   **Solution:** startPeriod must be ≥ 0

2. **Periods not in ascending order**
   ```json
   "chargingSchedulePeriod": [
     {"startPeriod": 3600, "limit": 7000},
     {"startPeriod": 0, "limit": 22000}    // ❌ Should be first!
   ]
   ```
   **Solution:** Sort by startPeriod ascending:
   ```json
   "chargingSchedulePeriod": [
     {"startPeriod": 0, "limit": 22000},
     {"startPeriod": 3600, "limit": 7000}
   ]
   ```

3. **Invalid time-of-day for Recurring**
   ```json
   {
     "chargingProfileKind": "Recurring",
     "recurrencyKind": "Daily",
     "chargingSchedule": {
       "startSchedule": "2026-01-08T18:00:00+00:00",  // ❌ Absolute time!
       "chargingSchedulePeriod": [...]
     }
   }
   ```
   **Solution:** For Recurring profiles, use relative times (seconds from midnight):
   ```json
   {
     "chargingProfileKind": "Recurring",
     "recurrencyKind": "Daily",
     "chargingSchedule": {
       "chargingSchedulePeriod": [
         {"startPeriod": 0, "limit": 22000},        // 00:00
         {"startPeriod": 64800, "limit": 7000}      // 18:00 (18*3600)
       ]
     }
   }
   ```

4. **Peak hours exceed 24 hours**
   ```json
   {
     "peak_start_hour": 18,
     "peak_end_hour": 26  // ❌ Invalid hour!
   }
   ```
   **Solution:** Hours must be 0-23

---

### Problem: Profile Stacking Issues

**Symptoms:**
- Multiple profiles don't combine as expected
- Composite schedule incorrect

**Common issues:**

1. **Multiple ChargePointMaxProfiles with same stackLevel**
   ```
   Profile A: stackLevel 0, limit 7400W
   Profile B: stackLevel 0, limit 11000W
   Result: Unpredictable (station picks one)
   ```
   **Solution:** Assign different stackLevels:
   ```
   Profile A: stackLevel 0, limit 7400W   (higher priority)
   Profile B: stackLevel 1, limit 11000W  (lower priority)
   Result: 7400W (lower stackLevel wins)
   ```

2. **TxProfile without transaction ID**
   ```json
   {
     "chargingProfilePurpose": "TxProfile",
     // Missing transactionId!
     "chargingSchedule": {...}
   }
   ```
   **Solution:** Always include transactionId for TxProfile:
   ```json
   {
     "chargingProfilePurpose": "TxProfile",
     "transactionId": 1234,
     "chargingSchedule": {...}
   }
   ```

3. **Connector mismatch**
   ```
   Profile sent to connector 1
   Composite schedule requested for connector 2
   Result: Empty schedule
   ```
   **Solution:** 
   - Send to connector 0 for station-wide
   - Or send to specific connector and request same connector

---

### Problem: Empty Composite Schedule

**Symptoms:**
- GET composite_schedule returns empty or null
- No schedule periods shown

**Possible causes:**

1. **No active profiles**
   ```
   Solution: Send at least one profile first
   ```

2. **All profiles expired**
   ```json
   "validTo": "2026-01-07T12:00:00+00:00"  // Past date
   ```
   **Solution:** Clear expired profiles, send new ones

3. **Wrong connector requested**
   ```bash
   # Profile sent to connector 1
   curl ".../composite_schedule?connector_id=2"  // Wrong!
   ```
   **Solution:** Request correct connector

4. **Duration too long**
   ```bash
   curl ".../composite_schedule?duration=999999"  // Very long
   ```
   **Solution:** Use reasonable duration (3600-86400 seconds)

---

### Problem: Station Stops Responding

**Symptoms:**
- Station disconnects from CSMS
- API calls timeout
- No logs generated

**Possible causes:**

1. **Too many profiles sent rapidly**
   ```
   Solution: Add delay between profile sends (1-2 seconds)
   ```

2. **Invalid profile crashes station logic**
   ```
   Solution: Validate profile structure before sending
   ```

3. **CSMS connection lost**
   ```
   Check: csms_server.py still running?
   Solution: Restart CSMS and reconnect station
   ```

**Recovery steps:**
```bash
# 1. Stop station
curl -X POST http://localhost:8000/stations/PY-SIM-0001/stop

# 2. Clear all profiles
curl -X DELETE http://localhost:8000/stations/PY-SIM-0001/charging_profile

# 3. Restart station
curl -X POST http://localhost:8000/stations/PY-SIM-0001/start

# 4. Wait for connection
sleep 5

# 5. Try again with valid profile
curl -X POST http://localhost:8000/stations/PY-SIM-0001/test_profiles \
  -d '{"scenario": "peak_shaving", "connector_id": 1, "max_power_w": 7400}'
```

---

## Best Practices

### Profile Design

✅ **DO:**
- Start with simple profiles (single period, Absolute kind)
- Use descriptive profile IDs (1, 2, 3...)
- Set stackLevel explicitly (don't rely on defaults)
- Test on single station before deploying fleet-wide
- Include validTo for temporary profiles
- Use TxDefaultProfile for general limits
- Use TxProfile only for specific transactions
- Send to connector 0 for station-wide limits

❌ **DON'T:**
- Send multiple profiles with same stackLevel and purpose
- Use very long durations (>24 hours) without testing
- Forget to clear profiles when changing strategy
- Set limits below station minimum (typically 1.4kW)
- Use TxProfile without transactionId
- Mix Absolute and Recurring times in same profile

---

### Stack Level Strategy

Recommended stackLevel allocation:

```
stackLevel 0  → Critical limits (grid constraints, circuit breakers)
stackLevel 1  → Business logic (peak shaving, load balancing)
stackLevel 2  → Optimization (TOU pricing, cost minimization)
stackLevel 3+ → User preferences, defaults
```

**Example:**
```json
// stackLevel 0: Circuit breaker limit (32A @ 230V)
{
  "stackLevel": 0,
  "chargingProfilePurpose": "ChargePointMaxProfile",
  "limit": 7400
}

// stackLevel 1: Peak shaving during business hours
{
  "stackLevel": 1,
  "chargingProfilePurpose": "TxDefaultProfile",
  "limit": 11000
}

// stackLevel 2: Off-peak optimization
{
  "stackLevel": 2,
  "chargingProfilePurpose": "TxDefaultProfile",
  "limit": 22000
}
```

---

### Testing Workflow

1. **Development testing:**
   ```bash
   # Test on single station
   python csms_server.py
   uvicorn controller_api:app --reload
   
   # Send test profile
   curl -X POST .../test_profiles -d '{"scenario": "peak_shaving", ...}'
   
   # Monitor logs
   tail -f reports/PY-SIM-0001/logs.txt
   
   # Verify enforcement
   curl .../composite_schedule
   ```

2. **Validation:**
   - Check OCPP status turns green
   - Verify power stays under limit
   - Test profile clearing
   - Confirm legacy policy after clear

3. **Load testing:**
   ```bash
   # Scale to multiple stations
   curl -X POST .../scale -d '{"target_count": 10}'
   
   # Send profiles to all
   for i in {1..10}; do
     curl -X POST .../stations/PY-SIM-000$i/test_profiles \
       -d '{"scenario": "peak_shaving", "max_power_w": 7400}'
   done
   
   # Monitor aggregate power
   curl .../stations/metrics
   ```

---

### Monitoring

**Key metrics to track:**

1. **Profile acceptance rate**
   ```
   Accepted profiles / Total sent
   Target: >95%
   ```

2. **Limit compliance**
   ```
   Time within limit / Total charging time
   Target: >99%
   ```

3. **API response times**
   ```
   P50, P95, P99 latencies
   Target: <500ms
   ```

4. **Dashboard indicators:**
   - Green OCPP badges (profiles active)
   - Blue Policy badges (legacy behavior)
   - Red badges (problematic - investigate)

---

## Advanced Topics

### Dynamic Load Management

Adjust profiles based on real-time grid capacity:

```python
import requests

# Get current grid capacity
grid_capacity = get_grid_capacity()  # e.g., 100kW

# Calculate per-station limit
num_stations = 10
per_station_limit = grid_capacity / num_stations  # 10kW each

# Send profiles to all stations
for station_id in stations:
    requests.post(
        f"http://localhost:8000/stations/{station_id}/test_profiles",
        json={
            "scenario": "peak_shaving",
            "connector_id": 1,
            "max_power_w": int(per_station_limit * 1000)
        }
    )
```

---

### Solar-Responsive Charging

Adjust charging rates based on solar production:

```python
# Morning: low solar, limit charging
send_profile(station_id, limit=7400)  # 7.4kW

# Midday: high solar, allow full power
clear_profile(station_id)  # Remove limits

# Evening: solar declining, moderate limit
send_profile(station_id, limit=11000)  # 11kW
```

---

### Cost Optimization

Integrate with electricity pricing API:

```python
import requests
from datetime import datetime, timedelta

# Get day-ahead prices
prices = get_electricity_prices()

# Find cheapest hours
cheap_hours = [h for h, p in prices.items() if p < threshold]

# Create time-of-use profile
profile = create_time_of_use_profile(
    off_peak_hours=cheap_hours,
    off_peak_limit=22000,
    peak_limit=7000
)

# Send to station
requests.post(f".../charging_profile", json={"profile": profile})
```

---

### Fleet Management

Manage multiple stations efficiently:

```python
# Get all running stations
response = requests.get("http://localhost:8000/stations")
stations = [s for s in response.json() if s['running']]

# Send profiles in parallel
from concurrent.futures import ThreadPoolExecutor

def send_profile(station_id):
    return requests.post(
        f".../stations/{station_id}/test_profiles",
        json={"scenario": "peak_shaving", "max_power_w": 7400}
    )

with ThreadPoolExecutor(max_workers=10) as executor:
    results = executor.map(send_profile, [s['id'] for s in stations])

# Check results
for station, result in zip(stations, results):
    print(f"{station['id']}: {result.json()['status']}")
```

---

## Appendix

### OCPP 1.6 Profile Structure

Complete structure reference:

```typescript
interface ChargingProfile {
  chargingProfileId: number;              // Unique ID
  transactionId?: number;                 // For TxProfile only
  stackLevel: number;                     // 0-99 (lower = higher priority)
  chargingProfilePurpose: 
    | "ChargePointMaxProfile"             // Station-wide limit
    | "TxDefaultProfile"                  // Default for transactions
    | "TxProfile";                        // Specific transaction
  chargingProfileKind:
    | "Absolute"                          // One-time schedule
    | "Recurring"                         // Repeating schedule
    | "Relative";                         // Relative to transaction start
  recurrencyKind?: "Daily" | "Weekly";   // For Recurring only
  validFrom?: string;                     // ISO 8601 timestamp
  validTo?: string;                       // ISO 8601 timestamp
  chargingSchedule: {
    duration?: number;                    // Seconds (omit for infinite)
    startSchedule?: string;               // ISO 8601 timestamp
    chargingRateUnit: "W" | "A";         // Watts or Amps
    chargingSchedulePeriod: Array<{
      startPeriod: number;                // Seconds from start
      limit: number;                      // Power/current limit
      numberPhases?: number;              // 1 or 3
    }>;
    minChargingRate?: number;            // Optional minimum
  };
}
```

### Charging Profile Purpose

| Purpose | Applies To | Priority | Use Case |
|---------|-----------|----------|----------|
| ChargePointMaxProfile | All connectors | Lowest | Station-wide limits |
| TxDefaultProfile | All transactions | Medium | Default behavior |
| TxProfile | Specific transaction | Highest | Per-session control |

### Charging Profile Kind

| Kind | Description | Use Case |
|------|-------------|----------|
| Absolute | One-time schedule | Temporary limits, events |
| Recurring | Repeating schedule | Time-of-use, daily patterns |
| Relative | Relative to transaction start | Per-session energy caps |

---

## Support

### Resources

- **OCPP 1.6 Specification:** https://www.openchargealliance.org/protocols/ocpp-16/
- **API Documentation:** http://localhost:8000/docs (Swagger UI)
- **Integration Tests:** `test_smartcharging_integration.py`

### Common Commands

```bash
# Start services
python csms_server.py
uvicorn controller_api:app --reload

# Open dashboard
open http://localhost:8000

# View API docs
open http://localhost:8000/docs

# Run integration tests
pytest test_smartcharging_integration.py -v

# Check station logs
tail -f reports/PY-SIM-0001/logs.txt

# Monitor all stations
watch -n 1 'curl -s localhost:8000/stations | jq'
```

---

**End of SmartCharging User Guide**

*Last updated: January 8, 2026*
