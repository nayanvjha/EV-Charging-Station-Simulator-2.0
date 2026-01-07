# SmartCharging Dashboard UI - User Guide

## Overview

The dashboard now includes full SmartCharging management capabilities with real-time OCPP status monitoring.

---

## New UI Components

### 1. SmartCharging Profile Management Panel

Located at the top of the controls section, this panel allows you to send test profiles to stations.

**Components:**
- **Station Selector** - Dropdown listing all running stations
- **Test Profile Buttons:**
  - 🔋 **7.4kW** - Sends peak shaving profile (7.4kW limit)
  - ⏰ **TOU** - Sends time-of-use profile (22kW off-peak, 7kW peak 18:00-22:00)
  - 📊 **30kWh** - Sends energy cap profile (30kWh max, 2 hours)
- **📋 View Profiles** - Opens profile viewer modal
- **🗑️ Clear All** - Clears all profiles from selected station

### 2. OCPP Status Column

New column in the stations table showing current control mode:

**Status Indicators:**

✅ **⚡ OCPP: X kW** (Green)
- Station is under OCPP SmartCharging control
- Shows current power limit
- Displays number of active profiles

✅ **✓ Policy: OK** (Blue)
- Station is using legacy policy engine
- No OCPP profiles active
- Charging normally

⚠️ **🔒 Policy: Blocked** (Red)
- Legacy policy is blocking charging
- Price or peak hour restriction
- No active transaction

### 3. Profile Viewer Modal

Click "View Profiles" to open detailed view:

**Sections:**
- **Station Info** - Shows station ID and connector
- **Active Profiles** - Lists current profiles with limits
- **Composite Schedule** - Visual timeline of power limits

**Actions:**
- 🔄 **Refresh** - Reload profile data
- 🗑️ **Clear All** - Remove all profiles
- **Close** - Close modal

---

## Visual Indicators

### Row Highlighting

Stations with active OCPP profiles are highlighted with:
- Green left border (3px)
- Subtle green background gradient
- Easy identification at a glance

### Status Badges

**OCPP Active (Green):**
```
⚡ OCPP: 7.4 kW
2 profile(s)
```

**Policy OK (Blue):**
```
✓ Policy: OK
Legacy control
```

**Policy Blocked (Red):**
```
🔒 Policy: Blocked
Price/Peak limit
```

---

## How to Use

### Sending Test Profiles

1. **Start a station** from the dashboard
2. **Select the station** from SmartCharging dropdown
3. **Click a test profile button:**
   - **7.4kW** for peak shaving
   - **TOU** for time-based control
   - **30kWh** for energy cap
4. **Watch the status update** - Profile appears in OCPP Status column within 5 seconds

### Viewing Profiles

1. **Select a station** with active profiles
2. **Click "View Profiles"** button
3. **Review active profiles** and schedule timeline
4. **Use Refresh** to update data
5. **Click Close** when done

### Clearing Profiles

**Option 1: From Panel**
1. Select station from dropdown
2. Click "Clear All" button
3. Confirm deletion
4. Status updates automatically

**Option 2: From Modal**
1. Open profile viewer
2. Click "Clear All" in modal footer
3. Confirm deletion
4. Modal refreshes automatically

---

## Auto-Polling Features

The dashboard automatically polls every 5 seconds for:

### OCPP Status Detection

- **Scans station logs** for OCPP messages
- **Extracts power limits** from log entries
- **Counts active profiles** from acceptance messages
- **Detects control mode** (OCPP vs legacy policy)

### Status Updates

Status is derived from station logs:

```javascript
// Detected from logs:
"OCPP limit: 7400W → 2.06Wh"  → OCPP Active (7.4 kW)
"Profile 1 accepted"           → Profile count incremented
"Policy blocked"               → Policy Blocked status
```

### Visual Feedback

- **Real-time updates** - No manual refresh needed
- **Smooth transitions** - Status changes animate
- **Color coding** - Instant mode recognition

---

## Profile Viewer Modal

### Active Profiles Section

Shows summary of current profiles:

```
┌─────────────────────────────────────┐
│ Active                    ⚡ 7.4 kW │
├─────────────────────────────────────┤
│ Profile Count: 2                    │
│ Current Limit: 7400 W               │
│ Control Mode: OCPP SmartCharging    │
└─────────────────────────────────────┘
```

### Composite Schedule Timeline

Visual representation of power limits over time:

```
T+0min   ████████████████ 7.4 kW
T+30min  ████████████     5.5 kW
T+60min  ████████████████ 7.4 kW

Duration: 3600s (60min)
```

**Features:**
- Bar width represents power limit
- Timeline shows start time of each period
- Duration shown at bottom
- Green bars with glow effect

---

## Button Reference

### Test Profile Buttons

**🔋 7.4kW Button**
- **Scenario:** peak_shaving
- **Limit:** 7400W
- **Profile Type:** ChargePointMaxProfile
- **Use Case:** Grid constraints, load balancing

**⏰ TOU Button**
- **Scenario:** time_of_use
- **Off-Peak:** 22000W (22kW)
- **Peak:** 7000W (7kW)
- **Peak Hours:** 18:00-22:00
- **Profile Type:** TxDefaultProfile
- **Recurrence:** Daily

**📊 30kWh Button**
- **Scenario:** energy_cap
- **Energy Limit:** 30000Wh (30kWh)
- **Duration:** 7200s (2 hours)
- **Power Limit:** 11000W (11kW)
- **Profile Type:** TxProfile

---

## Notifications

### Success Toast (Green)

Appears bottom-right when:
- Profile successfully sent
- Profiles cleared successfully
- Shows for 3 seconds

Example:
```
✓ Profile sent to PY-SIM-0001
```

### Error Toast (Red)

Appears bottom-right when:
- Station not found
- Profile rejected
- API error
- Shows for 4 seconds

Example:
```
Station PY-SIM-0001 not found or not connected
```

---

## Troubleshooting

### Status Not Updating

**Problem:** OCPP status shows "Policy: OK" after sending profile

**Solutions:**
1. Wait 5-10 seconds for next poll cycle
2. Check station logs manually (📋 button)
3. Look for "OCPP limit:" messages in logs
4. Verify profile was accepted (check success toast)

### Profile Not Found in Modal

**Problem:** Modal shows "No active profiles"

**Possible Causes:**
- Station doesn't support SmartCharging
- Profile was rejected by station
- Profile expired or cleared
- Station not connected to CSMS

**Solutions:**
1. Check CSMS server is running
2. Verify station is connected
3. Resend profile
4. Check station logs for errors

### Station Not in Dropdown

**Problem:** Station not appearing in SmartCharging selector

**Cause:** Only running stations appear in dropdown

**Solution:** Start the station first, then it will appear

### Modal Won't Load

**Problem:** Profile viewer shows "Loading..." indefinitely

**Possible Causes:**
- Station not connected
- API endpoint unavailable
- Network error

**Solutions:**
1. Verify API is running
2. Check browser console for errors
3. Try refreshing page
4. Restart station

---

## API Integration

### Endpoints Used

The UI calls these REST API endpoints:

1. **POST** `/stations/{station_id}/test_profiles`
   - Sends test profiles
   - Called by: Test profile buttons

2. **GET** `/stations/{station_id}/composite_schedule`
   - Fetches schedule
   - Called by: Profile viewer modal

3. **DELETE** `/stations/{station_id}/charging_profile`
   - Clears profiles
   - Called by: Clear All buttons

4. **GET** `/stations/{station_id}/logs`
   - Fetches logs for status detection
   - Called by: Auto-polling (every 5s)

### Status Detection Logic

```javascript
// Pseudo-code for status detection
for each log entry:
  if log contains "OCPP limit: XW":
    status = "OCPP Active"
    extract limit from log
  else if log contains "blocked" or "stop charging":
    status = "Policy Blocked"
  else:
    status = "Policy OK"
```

---

## Best Practices

### Workflow

1. **Start stations** from dashboard
2. **Monitor initial status** (should show "Policy: OK")
3. **Select station** from SmartCharging dropdown
4. **Send test profile** appropriate for use case
5. **Wait for status update** (5-10 seconds)
6. **Verify OCPP control** (green badge, power limit shown)
7. **View profiles** if needed for details
8. **Monitor charging** - watch Usage column
9. **Check logs** to see OCPP limit enforcement
10. **Clear profiles** when testing complete

### Testing Profiles

**Peak Shaving Test:**
- Send 7.4kW profile
- Watch Usage column stay ≤7.4 kW
- Check logs for "OCPP limit: 7400W"

**Time-of-Use Test:**
- Send TOU profile (peak 18:00-22:00)
- Test during peak hours
- Verify limit changes at 18:00 and 22:00

**Energy Cap Test:**
- Send 30kWh profile
- Monitor Energy column
- Verify transaction stops at 30kWh

### Performance

- **Auto-polling** runs every 5 seconds
- **Minimal overhead** - only fetches logs for running stations
- **Cached data** - station list cached between polls
- **Efficient parsing** - only scans recent log entries

---

## Keyboard Shortcuts

None currently implemented, but could be added:

- `Ctrl+P` - Open profile panel
- `Ctrl+V` - View profiles for selected station
- `Ctrl+C` - Clear profiles
- `Esc` - Close modal

---

## Browser Compatibility

Tested and working on:
- ✅ Chrome 120+
- ✅ Firefox 120+
- ✅ Safari 17+
- ✅ Edge 120+

Requires:
- JavaScript enabled
- CSS Grid support
- Fetch API support
- CSS Custom Properties

---

## Accessibility

### Screen Readers

- All buttons have descriptive labels
- Status badges have text content
- Modal has proper ARIA attributes
- Focus management in modal

### Keyboard Navigation

- Tab through all interactive elements
- Enter to activate buttons
- Escape to close modal
- Focus trap in modal

### Color Contrast

- All text meets WCAG AA standards
- Status colors have sufficient contrast
- Fallback text for color-blind users

---

## Advanced Usage

### Custom Profiles

To send custom profiles (not test profiles):

1. Use the REST API directly:
```bash
curl -X POST http://localhost:8000/stations/PY-SIM-0001/charging_profile \
  -H "Content-Type: application/json" \
  -d '{"connector_id": 1, "profile": {...}}'
```

2. Check the API documentation: `SMARTCHARGING_REST_API.md`

### Bulk Operations

To send profiles to multiple stations:

```bash
for station in $(curl -s http://localhost:8000/stations | jq -r '.[].station_id'); do
  curl -X POST http://localhost:8000/stations/$station/test_profiles \
    -H "Content-Type: application/json" \
    -d '{"scenario": "peak_shaving", "connector_id": 1, "max_power_w": 7400}'
done
```

### Monitoring

Watch status changes in real-time:

```bash
# Monitor one station's logs
watch -n 1 'curl -s http://localhost:8000/stations/PY-SIM-0001/logs | jq -r ".logs[-5:][]"'
```

---

## Future Enhancements

Planned improvements:

- [ ] Profile editor in UI (custom profiles)
- [ ] Bulk profile operations (send to multiple stations)
- [ ] Profile scheduling (activate at specific time)
- [ ] Profile templates (save/load presets)
- [ ] Real-time WebSocket updates (no polling)
- [ ] Charging session preview (estimated cost/time)
- [ ] Historical profile viewer (past profiles)
- [ ] Export/import profiles (JSON)

---

## Related Documentation

- **API Reference:** [SMARTCHARGING_REST_API.md](SMARTCHARGING_REST_API.md)
- **Quick Start:** [SMARTCHARGING_QUICKSTART.md](SMARTCHARGING_QUICKSTART.md)
- **Implementation:** [SMARTCHARGING_API_SUMMARY.md](SMARTCHARGING_API_SUMMARY.md)
- **CSMS Helpers:** [CSMS_SMARTCHARGING_HELPERS.md](CSMS_SMARTCHARGING_HELPERS.md)

---

## Support

For issues or questions:

1. **Check browser console** for JavaScript errors
2. **Check station logs** for OCPP messages
3. **Verify API is running** at http://localhost:8000/docs
4. **Test REST API directly** with curl/Postman
5. **Review this guide** for common issues

---

**Dashboard UI Updated** ✓

Date: January 8, 2026
