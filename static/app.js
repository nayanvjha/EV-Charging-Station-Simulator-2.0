/* =========================================================
   GLOBAL STATE
   ========================================================= */

const API_BASE = window.location.origin;

let cachedStations = [];
let currentPrice = 20;
let basePrice = 20;
let currentModalStation = null;

// Cache for OCPP status
const ocppStatusCache = {};

/* =========================================================
   SPINNER OVERLAY CONTROL
   ========================================================= */

function showSpinner(text = "Launching stations…", subtext = "Please wait") {
  const overlay = document.getElementById("spinner-overlay");
  const spinnerText = document.getElementById("spinner-text");
  const spinnerSubtext = document.getElementById("spinner-subtext");
  
  if (overlay) {
    overlay.classList.remove("hidden");
    overlay.style.display = "flex";
    if (spinnerText) spinnerText.textContent = text;
    if (spinnerSubtext) spinnerSubtext.textContent = subtext;
  }
}

function hideSpinner() {
  const overlay = document.getElementById("spinner-overlay");
  if (overlay) {
    overlay.style.display = "none";
    overlay.classList.add("hidden");
  }
}

/* =========================================================
   APPLY CHARGING ROW ANIMATION
   ========================================================= */

function applyChargingRowAnimation(tbody) {
  if (!tbody) return;
  
  const rows = tbody.querySelectorAll("tr");
  rows.forEach(row => {
    const statusCell = row.querySelector("td:nth-child(3)");
    if (statusCell) {
      const badge = statusCell.querySelector(".badge-online");
      if (badge) {
        row.classList.add("charging-active");
      } else {
        row.classList.remove("charging-active");
      }
    }
  });
}

/* =========================================================
   INIT (SAFE)
   ========================================================= */

document.addEventListener("DOMContentLoaded", async () => {
  try {
    await loadPrice().catch(err => {
      console.warn("Price load failed, using default:", err);
      currentPrice = 20;
      basePrice = 20;
    });
    await fetchAll();
    setInterval(fetchAll, 5000);
    hideSpinner();
  } catch (e) {
    console.error(e);
    showError("App failed to initialize");
    hideSpinner();
  }
});

/* =========================================================
   MASTER FETCH
   ========================================================= */

async function fetchAll() {
  await fetchStations();
  await fetchTotals();
}

/* =========================================================
   STATIONS TABLE
   ========================================================= */

async function fetchStations() {
  try {
    const res = await fetch(`${API_BASE}/stations`);
    if (!res.ok) throw new Error("Failed to fetch stations");

    const stations = await res.json();
    cachedStations = stations;

    const statTotal = document.getElementById("stat-total");
    const statRunning = document.getElementById("stat-running");

    if (statTotal) statTotal.textContent = stations.length;
    if (statRunning)
      statRunning.textContent = stations.filter(s => s.running).length;

    // Update station select dropdown for SmartCharging
    updateStationSelect(stations);

    const tbody = document.getElementById("stations-body");
    if (!tbody) return;

    tbody.innerHTML = "";

    if (!stations.length) {
      tbody.innerHTML = `<tr><td colspan="8">No stations</td></tr>`;
      return;
    }

    stations.forEach(s => {
      const status = s.running
        ? `<span class="badge badge-online">online</span>`
        : `<span class="badge badge-offline">stopped</span>`;

      const usage = s.running ? `${s.usage_kw.toFixed(2)} kW` : "–";
      const energy = `${s.energy_kwh.toFixed(3)} kWh`;

      // OCPP Status - will be populated by polling
      const ocppStatus = ocppStatusCache[s.station_id] || {
        has_profiles: false,
        profile_count: 0,
        limit_w: null,
        control_mode: 'policy'
      };
      
      let ocppHTML = '';
      if (ocppStatus.has_profiles && ocppStatus.limit_w !== null) {
        const limitKw = (ocppStatus.limit_w / 1000).toFixed(1);
        ocppHTML = `<div class="ocpp-status ocpp-active">
          <span class="ocpp-badge ocpp-badge-active">⚡ OCPP: ${limitKw} kW</span>
          <small>${ocppStatus.profile_count} profile(s)</small>
        </div>`;
      } else if (ocppStatus.control_mode === 'policy_blocked') {
        ocppHTML = `<div class="ocpp-status ocpp-blocked">
          <span class="ocpp-badge ocpp-badge-blocked">🔒 Policy: Blocked</span>
          <small>Price/Peak limit</small>
        </div>`;
      } else {
        ocppHTML = `<div class="ocpp-status ocpp-policy">
          <span class="ocpp-badge ocpp-badge-policy">✓ Policy: OK</span>
          <small>Legacy control</small>
        </div>`;
      }

      const action = s.running
        ? `<button class="btn-danger" onclick="stopStation('${s.station_id}')">Stop</button>`
        : `<button class="btn-primary" onclick="startStation('${s.station_id}')">Start</button>`;

      const logsButton = `<button class="btn-ghost" onclick="toggleLogs('${s.station_id}')">📋 Logs</button>`;

      tbody.innerHTML += `
        <tr class="${ocppStatus.has_profiles ? 'ocpp-controlled' : ''}">
          <td>${s.station_id}</td>
          <td>${s.profile}</td>
          <td>${status}</td>
          <td>${usage}</td>
          <td>${energy}</td>
          <td>${ocppHTML}</td>
          <td>
            <div class="smart-charging">
              <div class="sc-bars">
                <div class="sc-bar" style="width: ${s.energy_percent}%"></div>
              </div>
              <small class="sc-text">
                ${s.energy_kwh.toFixed(1)}/${s.max_energy_kwh.toFixed(0)} kWh
                <br>
                Price: ₹${s.charge_if_price_below.toFixed(0)} 
                ${s.allow_peak ? '✓ Peak' : '✗ No Peak'}
              </small>
            </div>
          </td>
          <td>
            <div class="action-buttons">
              ${logsButton}
              ${action}
            </div>
          </td>
        </tr>
        <tr class="logs-row hidden" id="logs-row-${s.station_id}">
          <td colspan="8">
            <div class="logs-container" id="logs-${s.station_id}">
              <div class="logs-header">
                <h4>Activity Log</h4>
                <button class="btn-close" onclick="toggleLogs('${s.station_id}')">✕</button>
              </div>
              <div class="logs-content" id="logs-content-${s.station_id}">
                <div class="logs-loading">Loading logs...</div>
              </div>
            </div>
          </td>
        </tr>
      `;
    });
    
    // Apply charging animation to active rows
    applyChargingRowAnimation(tbody);

  } catch (err) {
    console.error(err);
    showError(err.message);
  }
}

/* =========================================================
   TOTALS
   ========================================================= */

async function fetchTotals() {
  try {
    const res = await fetch(`${API_BASE}/totals`);
    if (!res.ok) throw new Error("Failed to fetch totals");

    const data = await res.json();

    const totalEnergy = document.getElementById("total-energy");
    const totalEarnings = document.getElementById("total-earnings");

    if (totalEnergy)
      totalEnergy.textContent = data.total_energy_kwh.toFixed(3);

    if (totalEarnings)
      totalEarnings.textContent = data.total_earnings.toFixed(2);

  } catch (err) {
    console.error(err);
    showError(err.message);
  }
}

/* =========================================================
   SCALE STATIONS
   ========================================================= */

async function scaleStations() {
  const countEl = document.getElementById("scale-count");
  const profileEl = document.getElementById("scale-profile");

  if (!countEl || !profileEl) return;

  const count = parseInt(countEl.value, 10);
  const profile = profileEl.value;

  if (isNaN(count) || count < 0) {
    showError("Invalid station count");
    return;
  }

  showSpinner(`Scaling to ${count} stations…`, `Profile: ${profile}`);
  try {
    await apiPost("/stations/scale", { count, profile });
    await new Promise(r => setTimeout(r, 500));
    await fetchStations();
  } catch (err) {
    showError(err.message);
  } finally {
    hideSpinner();
  }
}

/* =========================================================
   SINGLE STATION (FORM)
   ========================================================= */

async function startSingle() {
  const idEl = document.getElementById("single-id");
  const profileEl = document.getElementById("single-profile");

  if (!idEl || !profileEl) return;

  const station_id = idEl.value;
  const profile = profileEl.value;

  if (!station_id) {
    showError("Station ID required");
    return;
  }

  showSpinner(`Starting ${station_id}…`, `Profile: ${profile}`);
  try {
    await apiPost("/stations/start", { station_id, profile });
    await new Promise(r => setTimeout(r, 300));
    await fetchStations();
  } catch (err) {
    showError(err.message);
  } finally {
    hideSpinner();
  }
}

async function stopSingle() {
  const idEl = document.getElementById("single-id");
  if (!idEl) return;

  const station_id = idEl.value;
  if (!station_id) {
    showError("Station ID required");
    return;
  }

  showSpinner(`Stopping ${station_id}…`, "Please wait");
  try {
    await apiPost("/stations/stop", { station_id });
    await new Promise(r => setTimeout(r, 300));
    await fetchStations();
  } catch (err) {
    showError(err.message);
  } finally {
    hideSpinner();
  }
}

/* =========================================================
   TABLE ACTION WRAPPERS (🔥 FIX)
   ========================================================= */

async function startStation(station_id) {
  showSpinner(`Starting ${station_id}…`, "Please wait");
  try {
    await apiPost("/stations/start", {
      station_id,
      profile: "default"
    });
    await new Promise(r => setTimeout(r, 300));
    await fetchStations();
  } catch (err) {
    showError(err.message);
  } finally {
    hideSpinner();
  }
}

async function stopStation(station_id) {
  showSpinner(`Stopping ${station_id}…`, "Please wait");
  try {
    await apiPost("/stations/stop", { station_id });
    await new Promise(r => setTimeout(r, 300));
    await fetchStations();
  } catch (err) {
    showError(err.message);
  } finally {
    hideSpinner();
  }
}

/* =========================================================
   BULK ACTIONS
   ========================================================= */

async function startAllStations() {
  const count = cachedStations.filter(s => !s.running).length;
  showSpinner(`Starting ${count} stations…`, "This may take a moment");
  
  try {
    for (const s of cachedStations.filter(s => !s.running)) {
      await apiPost("/stations/start", {
        station_id: s.station_id,
        profile: s.profile
      });
      await new Promise(r => setTimeout(r, 100));
    }
    await fetchStations();
  } catch (err) {
    showError(err.message);
  } finally {
    hideSpinner();
  }
}

async function stopAllStations() {
  const count = cachedStations.filter(s => s.running).length;
  showSpinner(`Stopping ${count} stations…`, "This may take a moment");
  
  try {
    for (const s of cachedStations.filter(s => s.running)) {
      await apiPost("/stations/stop", { station_id: s.station_id });
      await new Promise(r => setTimeout(r, 100));
    }
    await fetchStations();
  } catch (err) {
    showError(err.message);
  } finally {
    hideSpinner();
  }
}

/* =========================================================
   PRICE CONTROL
   ========================================================= */

async function loadPrice() {
  try {
    const res = await fetch("/pricing");
    if (!res.ok) throw new Error("Failed to load price");
    const data = await res.json();

    currentPrice = data.price;
    basePrice = data.price;

    updatePriceUI();
  } catch (err) {
    console.warn("Price endpoint failed, using default:", err);
    currentPrice = 20;
    basePrice = 20;
    updatePriceUI();
  }
}

function updatePriceUI() {
  const el = document.getElementById("current-price");
  if (!el) return;
  el.textContent = currentPrice.toFixed(2);
}

async function pushPrice() {
  await fetch("/pricing", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ price: currentPrice })
  });
}

function increasePrice() {
  currentPrice += 1;
  updatePriceUI();
  pushPrice();
}

function decreasePrice() {
  if (currentPrice <= 1) return;
  currentPrice -= 1;
  updatePriceUI();
  pushPrice();
}

function resetPrice() {
  currentPrice = basePrice;
  updatePriceUI();
  pushPrice();
}

/* =========================================================
   LOG HANDLING
   ========================================================= */

// Cache for logs to avoid refetching
const logsCache = {};

async function toggleLogs(stationId) {
  const logsRow = document.getElementById(`logs-row-${stationId}`);
  const logsContent = document.getElementById(`logs-content-${stationId}`);

  if (!logsRow || !logsContent) return;

  const isHidden = logsRow.classList.contains("hidden");

  if (isHidden) {
    // Show logs
    logsRow.classList.remove("hidden");
    
    // Fetch if not cached
    if (!logsCache[stationId]) {
      await fetchAndDisplayLogs(stationId);
    } else {
      // Display cached logs
      displayLogs(stationId, logsCache[stationId]);
    }
  } else {
    // Hide logs
    logsRow.classList.add("hidden");
  }
}

async function fetchAndDisplayLogs(stationId) {
  const logsContent = document.getElementById(`logs-content-${stationId}`);
  if (!logsContent) return;

  try {
    logsContent.innerHTML = '<div class="logs-loading">Loading logs...</div>';
    
    const res = await fetch(`${API_BASE}/stations/${stationId}/logs`);
    
    if (!res.ok) {
      if (res.status === 404) {
        logsContent.innerHTML = '<div class="logs-error">Station not found</div>';
      } else {
        logsContent.innerHTML = '<div class="logs-error">Failed to fetch logs</div>';
      }
      return;
    }

    const data = await res.json();
    logsCache[stationId] = data.logs;
    displayLogs(stationId, data.logs);

  } catch (err) {
    console.error(`Error fetching logs for ${stationId}:`, err);
    logsContent.innerHTML = '<div class="logs-error">Error loading logs</div>';
  }
}

function displayLogs(stationId, logs) {
  const logsContent = document.getElementById(`logs-content-${stationId}`);
  if (!logsContent) return;

  if (!logs || logs.length === 0) {
    logsContent.innerHTML = '<div class="logs-empty">No logs yet</div>';
    return;
  }

  // Display logs with most recent first
  const logsHTML = logs.slice().reverse().map(log => {
    return `<div class="log-entry">${escapeHtml(log)}</div>`;
  }).join('');

  logsContent.innerHTML = logsHTML;
}

function escapeHtml(text) {
  const map = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#039;'
  };
  return text.replace(/[&<>"']/g, m => map[m]);
}

/* =========================================================
   API HELPER
   ========================================================= */

async function apiPost(path, body) {
  const res = await fetch(API_BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Request failed");
  }
}

/* =========================================================
   ERROR TOAST
   ========================================================= */

function showError(msg) {
  const toast = document.getElementById("error-toast");
  const message = document.getElementById("error-message");

  if (!toast || !message) {
    alert(msg);
    return;
  }

  message.textContent = msg;
  toast.classList.add("show");

  setTimeout(() => toast.classList.remove("show"), 4000);
}

function showSuccess(msg) {
  const toast = document.getElementById("success-toast");
  const message = document.getElementById("success-message");

  if (!toast || !message) return;

  message.textContent = msg;
  toast.classList.add("show");

  setTimeout(() => toast.classList.remove("show"), 3000);
}

/* =========================================================
   SMARTCHARGING FUNCTIONS
   ========================================================= */

function updateStationSelect(stations) {
  const select = document.getElementById("sc-station-select");
  if (!select) return;
  
  const currentValue = select.value;
  select.innerHTML = '<option value="">Select a station...</option>';
  
  stations.filter(s => s.running).forEach(s => {
    const option = document.createElement('option');
    option.value = s.station_id;
    option.textContent = s.station_id;
    select.appendChild(option);
  });
  
  // Restore selection if it still exists
  if (currentValue && stations.find(s => s.station_id === currentValue)) {
    select.value = currentValue;
  }
}

async function sendTestProfile(scenario, param) {
  const select = document.getElementById("sc-station-select");
  if (!select || !select.value) {
    showError("Please select a station first");
    return;
  }
  
  const stationId = select.value;
  
  let payload = {
    scenario: scenario,
    connector_id: 1
  };
  
  if (scenario === 'peak_shaving') {
    payload.max_power_w = param || 7400;
    showSpinner(`Sending ${(param/1000).toFixed(1)}kW limit to ${stationId}...`, "Peak shaving profile");
  } else if (scenario === 'time_of_use') {
    payload.off_peak_w = 22000;
    payload.peak_w = 7000;
    payload.peak_start_hour = 18;
    payload.peak_end_hour = 22;
    showSpinner(`Sending time-of-use profile to ${stationId}...`, "18:00-22:00 peak hours");
  } else if (scenario === 'energy_cap') {
    payload.transaction_id = Math.floor(Math.random() * 10000);
    payload.max_energy_wh = param || 30000;
    payload.duration_seconds = 7200;
    payload.power_limit_w = 11000;
    showSpinner(`Sending ${(param/1000).toFixed(0)}kWh cap to ${stationId}...`, "Energy cap profile");
  }
  
  try {
    const res = await fetch(`${API_BASE}/stations/${stationId}/test_profiles`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    
    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || 'Failed to send profile');
    }
    
    const data = await res.json();
    
    if (data.send_status === 'Accepted') {
      showSuccess(`✓ Profile sent to ${stationId}`);
      // Refresh OCPP status
      setTimeout(() => updateOCPPStatus(stationId), 500);
    } else {
      showError(`Profile rejected: ${data.send_status}`);
    }
    
  } catch (err) {
    showError(err.message);
  } finally {
    hideSpinner();
  }
}

async function viewProfiles() {
  const select = document.getElementById("sc-station-select");
  if (!select || !select.value) {
    showError("Please select a station first");
    return;
  }
  
  currentModalStation = select.value;
  openProfileModal(currentModalStation);
}

async function clearAllProfiles() {
  const select = document.getElementById("sc-station-select");
  if (!select || !select.value) {
    showError("Please select a station first");
    return;
  }
  
  const stationId = select.value;
  
  if (!confirm(`Clear all charging profiles from ${stationId}?`)) {
    return;
  }
  
  showSpinner(`Clearing profiles from ${stationId}...`, "Please wait");
  
  try {
    const res = await fetch(`${API_BASE}/stations/${stationId}/charging_profile`, {
      method: 'DELETE'
    });
    
    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || 'Failed to clear profiles');
    }
    
    const data = await res.json();
    
    if (data.status === 'success') {
      showSuccess(`✓ Profiles cleared from ${stationId}`);
      // Refresh OCPP status
      setTimeout(() => updateOCPPStatus(stationId), 500);
    } else {
      showError(`Failed to clear: ${data.status}`);
    }
    
  } catch (err) {
    showError(err.message);
  } finally {
    hideSpinner();
  }
}

async function updateOCPPStatus(stationId) {
  try {
    // Check station logs for OCPP limit messages
    const res = await fetch(`${API_BASE}/stations/${stationId}/logs`);
    if (!res.ok) return;
    
    const data = await res.json();
    const logs = data.logs || [];
    
    // Look for recent OCPP limit messages
    let hasProfiles = false;
    let limitW = null;
    let profileCount = 0;
    
    for (const log of logs.slice(-50)) {
      if (log.includes('OCPP limit:')) {
        hasProfiles = true;
        // Extract limit from log: "OCPP limit: 7400W → 2.06Wh"
        const match = log.match(/OCPP limit: (\d+)W/);
        if (match) {
          limitW = parseInt(match[1]);
        }
      } else if (log.includes('Profile') && log.includes('accepted')) {
        profileCount++;
      }
    }
    
    // Check for policy blocking
    let controlMode = 'policy';
    for (const log of logs.slice(-20)) {
      if (log.includes('stop charging') || log.includes('blocked')) {
        controlMode = 'policy_blocked';
        break;
      }
    }
    
    if (hasProfiles) {
      controlMode = 'ocpp';
    }
    
    ocppStatusCache[stationId] = {
      has_profiles: hasProfiles,
      profile_count: profileCount,
      limit_w: limitW,
      control_mode: controlMode
    };
    
  } catch (err) {
    console.error(`Failed to update OCPP status for ${stationId}:`, err);
  }
}

async function updateAllOCPPStatus() {
  for (const station of cachedStations) {
    if (station.running) {
      await updateOCPPStatus(station.station_id);
    }
  }
  // Refresh table to show updated status
  await fetchStations();
}

// Profile Modal Functions
function openProfileModal(stationId) {
  currentModalStation = stationId;
  
  const modal = document.getElementById("profile-modal-overlay");
  const stationSpan = document.getElementById("profile-modal-station");
  
  if (modal) modal.classList.remove("hidden");
  if (stationSpan) stationSpan.textContent = stationId;
  
  loadProfileData(stationId);
}

function closeProfileModal() {
  const modal = document.getElementById("profile-modal-overlay");
  if (modal) modal.classList.add("hidden");
  currentModalStation = null;
}

function refreshProfileModal() {
  if (currentModalStation) {
    loadProfileData(currentModalStation);
  }
}

async function clearProfilesFromModal() {
  if (!currentModalStation) return;
  
  if (!confirm(`Clear all profiles from ${currentModalStation}?`)) {
    return;
  }
  
  try {
    const res = await fetch(`${API_BASE}/stations/${currentModalStation}/charging_profile`, {
      method: 'DELETE'
    });
    
    if (!res.ok) throw new Error('Failed to clear profiles');
    
    showSuccess('✓ Profiles cleared');
    refreshProfileModal();
    setTimeout(() => updateOCPPStatus(currentModalStation), 500);
    
  } catch (err) {
    showError(err.message);
  }
}

async function loadProfileData(stationId) {
  const profileList = document.getElementById("profile-list");
  const scheduleDiv = document.getElementById("composite-schedule");
  
  if (profileList) profileList.innerHTML = '<div class="profile-loading">Loading profiles...</div>';
  if (scheduleDiv) scheduleDiv.innerHTML = '<div class="profile-loading">Loading schedule...</div>';
  
  try {
    // Fetch composite schedule
    const res = await fetch(
      `${API_BASE}/stations/${stationId}/composite_schedule?connector_id=1&duration=3600&charging_rate_unit=W`
    );
    
    if (!res.ok) {
      if (res.status === 404) {
        profileList.innerHTML = '<div class="profile-error">Station not found or not connected</div>';
        scheduleDiv.innerHTML = '<div class="profile-error">N/A</div>';
        return;
      }
      throw new Error('Failed to fetch profiles');
    }
    
    const data = await res.json();
    
    if (data.status === 'success' && data.schedule) {
      displayCompositeSchedule(data.schedule);
      displayProfileSummary(stationId);
    } else {
      profileList.innerHTML = '<div class="profile-empty">No active profiles</div>';
      scheduleDiv.innerHTML = '<div class="profile-empty">No schedule available</div>';
    }
    
  } catch (err) {
    profileList.innerHTML = `<div class="profile-error">Error: ${err.message}</div>`;
    scheduleDiv.innerHTML = `<div class="profile-error">Error: ${err.message}</div>`;
  }
}

function displayProfileSummary(stationId) {
  const profileList = document.getElementById("profile-list");
  if (!profileList) return;
  
  const ocppStatus = ocppStatusCache[stationId];
  
  if (ocppStatus && ocppStatus.has_profiles && ocppStatus.limit_w) {
    const limitKw = (ocppStatus.limit_w / 1000).toFixed(1);
    profileList.innerHTML = `
      <div class="profile-item">
        <div class="profile-item-header">
          <span class="profile-badge">Active</span>
          <span class="profile-limit">⚡ ${limitKw} kW</span>
        </div>
        <div class="profile-item-body">
          <div><strong>Profile Count:</strong> ${ocppStatus.profile_count}</div>
          <div><strong>Current Limit:</strong> ${ocppStatus.limit_w} W</div>
          <div><strong>Control Mode:</strong> OCPP SmartCharging</div>
        </div>
      </div>
    `;
  } else {
    profileList.innerHTML = '<div class="profile-empty">No active profiles</div>';
  }
}

function displayCompositeSchedule(schedule) {
  const scheduleDiv = document.getElementById("composite-schedule");
  if (!scheduleDiv) return;
  
  if (!schedule.chargingSchedulePeriod || schedule.chargingSchedulePeriod.length === 0) {
    scheduleDiv.innerHTML = '<div class="profile-empty">No schedule periods</div>';
    return;
  }
  
  const unit = schedule.chargingRateUnit || 'W';
  const periods = schedule.chargingSchedulePeriod;
  
  let html = '<div class="schedule-timeline">';
  
  periods.forEach((period, idx) => {
    const startSec = period.startPeriod;
    const startMin = Math.floor(startSec / 60);
    const limit = period.limit;
    const limitDisplay = unit === 'W' ? `${(limit/1000).toFixed(1)} kW` : `${limit} A`;
    
    html += `
      <div class="schedule-period">
        <div class="schedule-time">T+${startMin}min</div>
        <div class="schedule-bar-container">
          <div class="schedule-bar" style="width: ${Math.min(limit/220, 100)}%"></div>
        </div>
        <div class="schedule-limit">${limitDisplay}</div>
      </div>
    `;
  });
  
  html += '</div>';
  
  if (schedule.duration) {
    html += `<div class="schedule-info">Duration: ${schedule.duration}s (${(schedule.duration/60).toFixed(0)}min)</div>`;
  }
  
  scheduleDiv.innerHTML = html;
}

// Update polling to include OCPP status
const originalFetchAll = fetchAll;
fetchAll = async function() {
  await originalFetchAll();
  await updateAllOCPPStatus();
};
