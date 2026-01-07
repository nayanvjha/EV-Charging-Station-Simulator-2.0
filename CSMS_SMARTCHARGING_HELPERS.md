# CSMS SmartCharging Testing Helpers - Quick Reference

## Overview
Added comprehensive CSMS-side testing helpers to `csms_server.py` for OCPP 1.6 Smart Charging operations.

## Helper Functions (Module-Level)

### Profile Generators

#### 1. `create_charge_point_max_profile(profile_id, max_power_w)`
Creates a ChargePointMaxProfile limiting entire station power.

```python
from csms_server import create_charge_point_max_profile

# Limit entire charge point to 22kW
profile = create_charge_point_max_profile(
    profile_id=1,
    max_power_w=22000
)
```

**Use Case:** Grid constraints, building load limits, emergency power reduction

**Returns:** Complete OCPP profile dict ready for SetChargingProfile

---

#### 2. `create_time_of_use_profile(profile_id, off_peak_w, peak_w, peak_start_hour, peak_end_hour)`
Creates a daily recurring TxDefaultProfile with time-of-use rates.

```python
from csms_server import create_time_of_use_profile

# 11kW off-peak, 7kW during 8am-6pm peak
profile = create_time_of_use_profile(
    profile_id=2,
    off_peak_w=11000,
    peak_w=7000,
    peak_start_hour=8,
    peak_end_hour=18
)
```

**Use Case:** Dynamic pricing, load shifting, peak demand management

**Returns:** Recurring daily profile with 3 periods (off-peak → peak → off-peak)

---

#### 3. `create_energy_cap_profile(profile_id, transaction_id, max_energy_wh, duration_seconds, power_limit_w=11000)`
Creates a TxProfile for specific transaction with power/duration limits.

```python
from csms_server import create_energy_cap_profile

# Limit transaction 1234 to 30kWh over 2 hours at 11kW
profile = create_energy_cap_profile(
    profile_id=3,
    transaction_id=1234,
    max_energy_wh=30000,
    duration_seconds=7200,
    power_limit_w=11000
)
```

**Use Case:** Fleet charging, pay-per-use, energy quotas

**Returns:** Transaction-specific profile with duration and power limit

---

## CSMS Methods (CentralSystemChargePoint Class)

### 1. `send_charging_profile_to_station(connector_id, profile_dict)`

Send a charging profile to the station via SetChargingProfile.req.

```python
# When station connects
async def on_connect(websocket):
    csms_cp = CentralSystemChargePoint(station_id, websocket)
    
    # Generate profile
    profile = create_charge_point_max_profile(1, 22000)
    
    # Send to station
    response = await csms_cp.send_charging_profile_to_station(
        connector_id=0,  # 0 = charge point level
        profile_dict=profile
    )
    
    if response['status'] == 'Accepted':
        print(f"✓ Profile {response['profile_id']} set successfully")
    else:
        print(f"✗ Rejected: {response}")
```

**Parameters:**
- `connector_id` (int): 0 = charge point level, 1+ = specific connector
- `profile_dict` (dict): Complete OCPP profile from generator functions

**Returns:**
```python
{
    "status": "Accepted",  # or "Rejected"
    "connector_id": 0,
    "profile_id": 1
}
```

**Error Handling:**
```python
{
    "status": "Error",
    "error": "Connection timeout",
    "connector_id": 0
}
```

---

### 2. `request_composite_schedule_from_station(connector_id, duration, charging_rate_unit="W")`

Request the merged schedule from all active profiles.

```python
# Query what limits are currently active
response = await csms_cp.request_composite_schedule_from_station(
    connector_id=1,
    duration=3600,  # 1 hour in seconds
    charging_rate_unit="W"  # or "A" for amps
)

if response['status'] == 'Accepted':
    schedule = response['chargingSchedule']
    periods = schedule['chargingSchedulePeriod']
    
    print(f"Composite schedule has {len(periods)} periods:")
    for p in periods:
        time_from_start = p['startPeriod']
        limit = p['limit']
        print(f"  {time_from_start}s: {limit}W")
```

**Parameters:**
- `connector_id` (int): Connector to query
- `duration` (int): Schedule duration in seconds
- `charging_rate_unit` (str): "W" for Watts or "A" for Amps

**Returns (on success):**
```python
{
    "status": "Accepted",
    "connector_id": 1,
    "schedule_start": "2026-01-08T10:00:00Z",
    "chargingSchedule": {
        "chargingRateUnit": "W",
        "chargingSchedulePeriod": [
            {"startPeriod": 0, "limit": 11000},
            {"startPeriod": 1800, "limit": 7000}
        ],
        "duration": 3600
    }
}
```

**Returns (no profiles):**
```python
{
    "status": "Rejected",
    "connector_id": 1
}
```

---

### 3. `clear_charging_profile_from_station(profile_id=None, connector_id=None, purpose=None, stack_level=None)`

Clear profiles with optional filters (AND logic).

```python
# Clear specific profile
response = await csms_cp.clear_charging_profile_from_station(
    profile_id=1
)

# Clear all profiles on connector 1
response = await csms_cp.clear_charging_profile_from_station(
    connector_id=1
)

# Clear all TxDefaultProfile profiles
response = await csms_cp.clear_charging_profile_from_station(
    purpose="TxDefaultProfile"
)

# Clear all profiles everywhere
response = await csms_cp.clear_charging_profile_from_station(
    connector_id=0
)

# Clear with multiple filters (AND logic)
response = await csms_cp.clear_charging_profile_from_station(
    connector_id=1,
    purpose="TxDefaultProfile",
    stack_level=0
)
```

**Parameters (all optional):**
- `profile_id` (int): Specific profile ID
- `connector_id` (int): Connector to clear (0 = all connectors)
- `purpose` (str): "ChargePointMaxProfile", "TxDefaultProfile", or "TxProfile"
- `stack_level` (int): Stack level filter

**Returns:**
```python
{
    "status": "Accepted",  # or "Unknown" if none matched
    "filters": {
        "profile_id": 1,
        "connector_id": None,
        "purpose": None,
        "stack_level": None
    }
}
```

---

## Complete Usage Example

```python
import asyncio
from csms_server import (
    CentralSystemChargePoint,
    create_charge_point_max_profile,
    create_time_of_use_profile,
    create_energy_cap_profile,
)

async def manage_charging(csms_cp: CentralSystemChargePoint):
    """Example CSMS charging management workflow."""
    
    # 1. Set station-wide limit during grid event
    print("1. Setting 11kW station-wide limit...")
    profile1 = create_charge_point_max_profile(100, 11000)
    response = await csms_cp.send_charging_profile_to_station(0, profile1)
    print(f"   → {response['status']}")
    
    # 2. Configure time-of-use for connector 1
    print("2. Setting time-of-use profile...")
    profile2 = create_time_of_use_profile(101, 11000, 7000, 8, 18)
    response = await csms_cp.send_charging_profile_to_station(1, profile2)
    print(f"   → {response['status']}")
    
    # 3. Check current limits
    print("3. Requesting composite schedule...")
    response = await csms_cp.request_composite_schedule_from_station(1, 3600)
    if response['status'] == 'Accepted':
        periods = response['chargingSchedule']['chargingSchedulePeriod']
        print(f"   → {len(periods)} active periods")
        for p in periods:
            print(f"      {p['startPeriod']}s: {p['limit']}W")
    
    # 4. Transaction starts, add specific limit
    print("4. Setting transaction-specific profile...")
    profile3 = create_energy_cap_profile(102, 1234, 30000, 7200, 11000)
    response = await csms_cp.send_charging_profile_to_station(1, profile3)
    print(f"   → {response['status']}")
    
    # 5. Grid event over, clear station-wide limit
    print("5. Clearing ChargePointMaxProfile...")
    response = await csms_cp.clear_charging_profile_from_station(
        purpose="ChargePointMaxProfile"
    )
    print(f"   → {response['status']}")
    
    # 6. Transaction ends, clear its profile
    print("6. Clearing transaction profile...")
    response = await csms_cp.clear_charging_profile_from_station(profile_id=102)
    print(f"   → {response['status']}")
    
    print("\nCharging management workflow complete!")

# Use in CSMS on_connect handler
async def on_connect(websocket, station_id):
    csms_cp = CentralSystemChargePoint(station_id, websocket)
    
    # Start OCPP message loop
    asyncio.create_task(csms_cp.start())
    
    # Manage charging (example workflow)
    await asyncio.sleep(5)  # Wait for station to boot
    await manage_charging(csms_cp)
```

---

## Profile Priority Rules

### Purpose Priority (highest to lowest):
1. **ChargePointMaxProfile** → Station-wide maximum (connector 0)
2. **TxProfile** → Specific transaction (overrides TxDefault for that TX)
3. **TxDefaultProfile** → Default for all transactions on connector

### Stacking Behavior:
- Station takes **MINIMUM** limit from all applicable profiles
- ChargePointMax on connector 0 applies to ALL connectors
- Lower stackLevel = higher priority within same purpose
- Multiple profiles combine using MIN operation

### Example:
```
Active profiles:
  ChargePointMaxProfile (connector 0): 22kW
  TxDefaultProfile (connector 1):      11kW  
  TxProfile (tx 1234, connector 1):     7kW

Result:
  Transaction 1234 on connector 1: min(22, 11, 7) = 7kW ✓
  Other TXs on connector 1:        min(22, 11)    = 11kW ✓
```

---

## Common Use Cases

### Grid Constraint Management
```python
# Emergency: Reduce all stations to 50%
profile = create_charge_point_max_profile(999, 11000)
await csms_cp.send_charging_profile_to_station(0, profile)
```

### Time-of-Use Optimization
```python
# Daily schedule: cheaper at night
profile = create_time_of_use_profile(1000, 11000, 7000, 8, 18)
await csms_cp.send_charging_profile_to_station(1, profile)
```

### Fleet Charging Quotas
```python
# Vehicle needs exactly 30kWh
profile = create_energy_cap_profile(1001, tx_id, 30000, 7200)
await csms_cp.send_charging_profile_to_station(1, profile)
```

### Load Balancing
```python
# 4 stations sharing 44kW capacity
for i, station in enumerate(stations):
    profile = create_charge_point_max_profile(i, 11000)
    await station.send_charging_profile_to_station(0, profile)
```

---

## Error Handling

All methods return dict with status and handle exceptions gracefully:

```python
response = await csms_cp.send_charging_profile_to_station(1, profile)

if response['status'] == 'Accepted':
    # Success
    print(f"Profile {response['profile_id']} active")
    
elif response['status'] == 'Rejected':
    # Station rejected (validation failed, conflict, etc.)
    print("Profile rejected by station")
    
elif response['status'] == 'Error':
    # Communication error, timeout, exception
    print(f"Error: {response['error']}")
```

---

## Testing

Run the demo script to see all functionality:

```bash
python csms_smartcharging_demo.py
```

This demonstrates:
- ✓ Profile generator functions
- ✓ API usage patterns
- ✓ Common use cases
- ✓ Priority and stacking rules

---

## Integration with Station

The station side automatically:
1. Receives SetChargingProfile.req
2. Validates and stores profiles
3. Applies limits during MeterValues loop
4. Takes minimum of all active profiles
5. Falls back to legacy policy when no profiles active

See [OCPP_SMARTCHARGING_INTEGRATION.md](OCPP_SMARTCHARGING_INTEGRATION.md) for station-side details.

---

## Files Modified

- ✅ `csms_server.py` - Added 3 async methods + 3 helper functions
- ✅ `csms_smartcharging_demo.py` - Comprehensive demonstration script (NEW)

## Implementation Date
January 8, 2026
