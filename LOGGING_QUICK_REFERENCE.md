# Per-Station Logging System - Quick Reference

## 🚀 Quick Start

### View Logs for a Station
```bash
curl http://localhost:8000/stations/PY-SIM-0001/logs | jq
```

### Sample Response
```json
{
  "station_id": "PY-SIM-0001",
  "logs": [
    "[14:23:45] Station initialized",
    "[14:23:46] BootNotification sent",
    "[14:23:47] BootNotification accepted",
    "[14:23:48] Connector available",
    "[14:23:50] Authorization successful - ABC123",
    "[14:23:51] Charging started (price: $0.35, id_tag: ABC123)",
    "[14:24:05] Charging stopped (2.50 kWh delivered)"
  ],
  "count": 7
}
```

---

## 📍 Log Messages by Category

### Station Lifecycle
| Message | Meaning |
|---------|---------|
| `Station initialized` | ChargePoint object created |
| `Station startup initiated` | Begin boot sequence |
| `BootNotification sent` | Boot notification queued |
| `BootNotification accepted` | CSMS accepted boot |
| `BootNotification rejected: {status}` | CSMS rejected boot |
| `Connector available` | Status notification sent |
| `Heartbeat sent` | Keep-alive message sent |
| `Station shutting down` | Graceful shutdown initiated |

### Authorization & Transactions
| Message | Meaning |
|---------|---------|
| `Authorization successful - {id_tag}` | ID tag accepted by CSMS |
| `Authorization failed - {id_tag} ({status})` | ID tag rejected |
| `Charging started (price: ${price}, id_tag: {id_tag})` | Transaction started |
| `Charging stopped ({energy} kWh delivered)` | Transaction stopped |

### Smart Charging Decisions
| Message | Meaning |
|---------|---------|
| `Price too high (${price}) — waiting` | Skipped due to high price |
| `Peak hours ({hour}:00) and peak disabled — waiting` | Skipped due to peak hours |
| `Energy cap reached ({energy} kWh) — stopping` | Max energy limit hit |

---

## 🔧 Code Integration Points

### In station.py

#### Add logging to a new event:
```python
# Simple event
cp.log("My custom event")

# With variable data
cp.log(f"Transaction {transaction_id} started")

# With formatted numbers
cp.log(f"Price adjusted to ${new_price:.2f}")
```

#### Retrieve all logs:
```python
logs = cp.get_logs()
for entry in logs:
    print(entry)
```

### In controller_api.py

#### Get logs for a station:
```python
logs = manager.get_station_logs("PY-SIM-0001")
```

#### Use in an API endpoint:
```python
@app.get("/custom-logs/{station_id}")
async def get_custom_logs(station_id: str):
    logs = manager.get_station_logs(station_id)
    # Process logs here
    return {"data": logs}
```

---

## 📊 Data Structure

### Log Buffer
- **Type**: `collections.deque(maxlen=50)`
- **Max Entries**: 50 most recent logs
- **Auto-removal**: Oldest entry removed when 51st added
- **Performance**: O(1) append, O(1) iteration

### Log Entry Format
```
[HH:MM:SS] message
```
- **Timestamp**: 24-hour format, local system time
- **Message**: String describing the event

---

## 🎯 Common Tasks

### Task: Find when charging stopped
```bash
curl http://localhost:8000/stations/PY-SIM-0001/logs | \
  jq '.logs[] | select(contains("stopped"))'
```

### Task: Check if station is connected
```bash
curl http://localhost:8000/stations/PY-SIM-0001/logs | \
  jq '.logs[-1]'  # Get last log entry
```

### Task: Count how many times auth was attempted
```bash
curl http://localhost:8000/stations/PY-SIM-0001/logs | \
  jq '[.logs[] | select(contains("Authorization"))] | length'
```

### Task: Show all price-related decisions
```bash
curl http://localhost:8000/stations/PY-SIM-0001/logs | \
  jq '.logs[] | select(contains("Price") or contains("price"))'
```

---

## 🔍 Debugging Tips

### If no logs appear:
1. Check that station is actually running: `curl http://localhost:8000/stations`
2. Verify station_id is correct (case-sensitive)
3. Wait a few seconds for initial boot messages

### To see logs in real-time:
```bash
watch -n 1 'curl -s http://localhost:8000/stations/PY-SIM-0001/logs | jq .logs[-3:]'
```

### To export all logs:
```bash
curl http://localhost:8000/stations/PY-SIM-0001/logs | jq -r '.logs[]' > logs.txt
```

---

## 💾 Log Buffer Limits

- **Buffer Size**: 50 entries maximum
- **Memory Per Entry**: ~100 bytes average
- **Total Buffer Memory**: ~5 KB per station
- **Overlap Duration**: Depends on activity frequency
  - Active station: ~5-10 minutes of history
  - Idle station: Hours of history

---

## 🔐 Security Considerations

- Logs contain ID tags (e.g., "ABC123")
- Price information is included in logs
- Station IDs are visible
- **Recommendation**: Restrict log API to authenticated users in production

---

## 📈 Performance Impact

- **CPU**: Negligible (timestamp + append)
- **Memory**: ~5 KB per active station
- **I/O**: None (in-memory only)
- **Latency**: <1 ms per log call

---

## 🔄 Future Enhancements

- [ ] Persistent storage to database
- [ ] Log filtering/search capability
- [ ] Log level support (DEBUG, INFO, WARN, ERROR)
- [ ] Structured logging (JSON format)
- [ ] Log rotation/archival
- [ ] Dashboard visualization
- [ ] Real-time log streaming (WebSocket)
- [ ] Alerts on specific log patterns
