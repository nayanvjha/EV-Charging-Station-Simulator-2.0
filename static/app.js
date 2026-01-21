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

let securityEvents = [];
let securityStats = { by_type: {}, by_severity: {} };
let securityLivePolling = true;
let securityFilters = { severity: "all", station: "" };
let securityStationCounts = {};

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
    initApiKeyUI();
    initSecurityUI();
    if (!getApiKey()) {
      hideSpinner();
      return;
    }
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
  if (!getApiKey()) {
    return;
  }
  if (securityLivePolling) {
    await fetchSecurityData();
  }
  await fetchStations();
  await fetchTotals();
}

/* =========================================================
   STATIONS TABLE
   ========================================================= */

async function fetchStations() {
  try {
    const res = await apiFetch(`/stations`);
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
      tbody.innerHTML = `<tr><td colspan="9">No stations</td></tr>`;
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
      const alertSummary = getStationAlertSummary(s.station_id);
      const alertBadgeClass = alertSummary.critical > 0
        ? "alert-critical"
        : alertSummary.total > 0
          ? "alert-warning"
          : "alert-clear";
      const alertButton = `<button class="btn-ghost alert-btn" onclick="openSecurityPanel('${s.station_id}')">
          ⚠️ <span class="alert-badge ${alertBadgeClass}">${alertSummary.total}</span>
        </button>`;

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
          <td>${alertButton}</td>
          <td>
            <div class="action-buttons">
              ${logsButton}
              ${action}
            </div>
          </td>
        </tr>
        <tr class="logs-row hidden" id="logs-row-${s.station_id}">
          <td colspan="9">
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
    const res = await apiFetch(`/totals`);
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
   SECURITY SOC
   ========================================================= */

function initSecurityUI() {
  const severitySelect = document.getElementById("security-severity-filter");
  const stationSelect = document.getElementById("security-station-filter");
  const liveToggle = document.getElementById("security-live-toggle");

  if (severitySelect) {
    severitySelect.addEventListener("change", () => {
      securityFilters.severity = severitySelect.value;
      renderSecurityPanel();
    });
  }

  if (stationSelect) {
    stationSelect.addEventListener("change", () => {
      securityFilters.station = stationSelect.value;
      renderSecurityPanel();
    });
  }

  if (liveToggle) {
    liveToggle.addEventListener("change", () => {
      securityLivePolling = liveToggle.checked;
      if (securityLivePolling) {
        fetchSecurityData();
      }
    });
  }

  updateSecurityBadge();
}

function openSecurityPanel(stationId = "") {
  const overlay = document.getElementById("security-modal-overlay");
  if (overlay) {
    overlay.classList.remove("hidden");
  }
  if (stationId) {
    securityFilters.station = stationId;
    const stationSelect = document.getElementById("security-station-filter");
    if (stationSelect) stationSelect.value = stationId;
  }
  renderSecurityPanel();
  refreshSecurity();
}

function closeSecurityPanel() {
  const overlay = document.getElementById("security-modal-overlay");
  if (overlay) {
    overlay.classList.add("hidden");
  }
}

async function refreshSecurity() {
  await fetchSecurityData(true);
}

async function acknowledgeSecurity() {
  try {
    const res = await apiFetch(`/api/v1/security/clear`, { method: "DELETE" });
    if (!res.ok) throw new Error("Failed to clear alerts");
    securityEvents = [];
    securityStats = { by_type: {}, by_severity: {} };
    securityStationCounts = {};
    updateSecurityBadge();
    renderSecurityPanel();
    showSuccess("Security alerts cleared");
  } catch (err) {
    console.error(err);
    showError(err.message);
  }
}

async function generateTestAlert() {
  try {
    const res = await apiFetch(`/api/v1/security/attack`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        station_id: "PY-SIM-0001",
        action: "tamper_payload",
        target_message: "StartTransaction",
        corruption_type: "truncate_field",
        duration: 20,
        allow_unowned: true
      })
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || "Failed to generate test alert");
    }
    showSuccess("Test alert generated");
    await refreshSecurity();
  } catch (err) {
    console.error(err);
    showError(err.message);
  }
}

async function fetchSecurityData(force = false) {
  if (!getApiKey()) return;
  try {
    const [eventsRes, statsRes] = await Promise.all([
      apiFetch(`/api/v1/security/events?limit=200`),
      apiFetch(`/api/v1/security/stats`),
    ]);

    if (eventsRes.ok) {
      const data = await eventsRes.json();
      securityEvents = Array.isArray(data.events) ? data.events : [];
      securityStationCounts = buildStationAlertCounts(securityEvents);
    }

    if (statsRes.ok) {
      securityStats = await statsRes.json();
    }

    updateSecurityBadge();
    if (force || isSecurityPanelOpen()) {
      renderSecurityPanel();
    }
  } catch (err) {
    console.error(err);
  }
}

function isSecurityPanelOpen() {
  const overlay = document.getElementById("security-modal-overlay");
  return overlay && !overlay.classList.contains("hidden");
}

function updateSecurityBadge() {
  const badge = document.getElementById("security-badge");
  if (!badge) return;
  const criticalCount = securityEvents.filter(e => normalizeSeverity(e.severity) === "critical").length;
  badge.textContent = criticalCount;
  badge.classList.toggle("security-badge-critical", criticalCount > 0);
  badge.classList.toggle("security-badge-idle", criticalCount === 0);
}

function normalizeSeverity(severity) {
  const value = String(severity || "").toLowerCase();
  if (value === "high" || value === "critical") return "critical";
  if (value === "medium" || value === "warning") return "warning";
  return "info";
}

function buildStationAlertCounts(events) {
  const counts = {};
  events.forEach(event => {
    const stationId = event.station_id;
    if (!stationId) return;
    if (!counts[stationId]) counts[stationId] = { total: 0, critical: 0 };
    counts[stationId].total += 1;
    if (normalizeSeverity(event.severity) === "critical") {
      counts[stationId].critical += 1;
    }
  });
  return counts;
}

function getStationAlertSummary(stationId) {
  return securityStationCounts[stationId] || { total: 0, critical: 0 };
}

function getFilteredSecurityEvents() {
  return securityEvents.filter(event => {
    if (securityFilters.station && event.station_id !== securityFilters.station) {
      return false;
    }
    if (securityFilters.severity !== "all") {
      return normalizeSeverity(event.severity) === securityFilters.severity;
    }
    return true;
  });
}

function renderSecurityPanel() {
  renderSecurityTable();
  renderSecurityCharts();
}

function renderSecurityTable() {
  const tbody = document.getElementById("security-events-body");
  if (!tbody) return;
  const events = getFilteredSecurityEvents();
  if (!events.length) {
    tbody.innerHTML = `<tr><td colspan="5">No security alerts</td></tr>`;
    return;
  }
  tbody.innerHTML = events
    .map(event => {
      const severity = normalizeSeverity(event.severity);
      const time = new Date(event.timestamp).toLocaleString();
      return `
        <tr>
          <td>${time}</td>
          <td>${event.station_id}</td>
          <td>${event.event_type}</td>
          <td><span class="severity-tag severity-${severity}">${severity}</span></td>
          <td class="security-desc">${event.description}</td>
        </tr>
      `;
    })
    .join("");
}

function renderSecurityCharts() {
  const events = getFilteredSecurityEvents();
  const stats = computeStatsFromEvents(events);

  renderTypeBarChart(stats.by_type);
  renderSeverityDonut(stats.by_severity);
  renderSecurityTrend(events);
}

function computeStatsFromEvents(events) {
  const stats = { by_type: {}, by_severity: {} };
  events.forEach(event => {
    stats.by_type[event.event_type] = (stats.by_type[event.event_type] || 0) + 1;
    const severity = normalizeSeverity(event.severity);
    stats.by_severity[severity] = (stats.by_severity[severity] || 0) + 1;
  });
  return stats;
}

function renderTypeBarChart(typeCounts) {
  const container = document.getElementById("security-type-chart");
  if (!container) return;
  const entries = Object.entries(typeCounts);
  if (!entries.length) {
    container.innerHTML = `<div class="chart-empty">No data</div>`;
    return;
  }
  const max = Math.max(...entries.map(([, count]) => count));
  container.innerHTML = entries
    .map(([type, count]) => {
      const width = max ? Math.round((count / max) * 100) : 0;
      return `
        <div class="bar-row">
          <span class="bar-label">${type}</span>
          <div class="bar-track">
            <div class="bar-fill" style="width:${width}%"></div>
          </div>
          <span class="bar-value">${count}</span>
        </div>
      `;
    })
    .join("");
}

function renderSeverityDonut(severityCounts) {
  const chart = document.getElementById("security-severity-chart");
  const legend = document.getElementById("security-severity-legend");
  if (!chart || !legend) return;
  const totals = {
    info: severityCounts.info || 0,
    warning: severityCounts.warning || 0,
    critical: severityCounts.critical || 0,
  };
  const total = totals.info + totals.warning + totals.critical;
  if (!total) {
    chart.style.background = "conic-gradient(#334155 0 100%)";
    legend.innerHTML = `<div class="chart-empty">No data</div>`;
    return;
  }
  const infoPct = (totals.info / total) * 100;
  const warningPct = (totals.warning / total) * 100;
  const criticalPct = (totals.critical / total) * 100;
  chart.style.background = `conic-gradient(
    #64748b 0 ${infoPct}%,
    #f59e0b ${infoPct}% ${infoPct + warningPct}%,
    #ef4444 ${infoPct + warningPct}% 100%
  )`;
  legend.innerHTML = `
    <div class="legend-item"><span class="legend-dot dot-info"></span>Info ${totals.info}</div>
    <div class="legend-item"><span class="legend-dot dot-warning"></span>Warning ${totals.warning}</div>
    <div class="legend-item"><span class="legend-dot dot-critical"></span>Critical ${totals.critical}</div>
  `;
}

function renderSecurityTrend(events) {
  const svg = document.getElementById("security-trend");
  const label = document.getElementById("security-trend-label");
  if (!svg || !label) return;
  const now = Date.now();
  const start = now - 24 * 60 * 60 * 1000;
  const buckets = Array.from({ length: 24 }, () => 0);
  events.forEach(event => {
    const time = new Date(event.timestamp).getTime();
    if (Number.isNaN(time) || time < start) return;
    const index = Math.min(23, Math.max(0, Math.floor((time - start) / (60 * 60 * 1000))));
    buckets[index] += 1;
  });
  const max = Math.max(0, ...buckets);
  if (!max) {
    svg.innerHTML = "";
    label.textContent = "No data";
    return;
  }
  const points = buckets.map((value, index) => {
    const x = (index / 23) * 200;
    const y = 60 - (value / max) * 50 - 5;
    return `${x},${y}`;
  });
  svg.innerHTML = `
    <polyline
      fill="none"
      stroke="#22c55e"
      stroke-width="2"
      points="${points.join(" ")}" />
  `;
  const total = buckets.reduce((sum, val) => sum + val, 0);
  label.textContent = `${total} alerts in last 24h`;
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
    const res = await apiFetch("/pricing");
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
  await apiPost("/pricing", { price: currentPrice });
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
    
    const res = await apiFetch(`/stations/${stationId}/logs`);
    
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
  const res = await apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Request failed");
  }
}

function getApiKey() {
  return localStorage.getItem("apiKey") || "";
}

function saveApiKey() {
  const input = document.getElementById("api-key-input");
  if (!input) return;
  const value = input.value.trim();
  if (!value) {
    showError("API key required");
    return;
  }
  localStorage.setItem("apiKey", value);
  updateApiKeyStatus();
  loadPrice();
  fetchAll();
}

function clearApiKey() {
  localStorage.removeItem("apiKey");
  const input = document.getElementById("api-key-input");
  if (input) input.value = "";
  updateApiKeyStatus();
}

function initApiKeyUI() {
  const input = document.getElementById("api-key-input");
  if (input) {
    input.value = getApiKey();
  }
  updateApiKeyStatus();
}

function updateApiKeyStatus() {
  const status = document.getElementById("api-key-status");
  const key = getApiKey();
  if (status) {
    status.textContent = key ? "API key set" : "Not set";
    status.classList.toggle("api-key-ok", Boolean(key));
  }
}

async function apiFetch(path, options = {}) {
  const key = getApiKey();
  const headers = options.headers ? { ...options.headers } : {};
  if (key) headers["x-api-key"] = key;
  const url = path.startsWith("http") ? path : `${API_BASE}${path}`;
  const res = await fetch(url, { ...options, headers });
  if (res.status === 401) {
    showError("Unauthorized: set a valid API key");
  }
  if (res.status === 429) {
    showError("Rate limit exceeded");
  }
  return res;
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
  const batterySelect = document.getElementById("battery-station-select");
  const securitySelect = document.getElementById("security-station-filter");
  if (!select && !batterySelect && !securitySelect) return;
  
  const currentValue = select ? select.value : "";
  if (select) {
    select.innerHTML = '<option value="">Select a station...</option>';
  }
  
  stations.filter(s => s.running).forEach(s => {
    const option = document.createElement('option');
    option.value = s.station_id;
    option.textContent = s.station_id;
    if (select) {
      select.appendChild(option);
    }
  });

  if (batterySelect) {
    const currentBatteryValue = batterySelect.value;
    batterySelect.innerHTML = '<option value="">Select a station...</option>';
    stations.filter(s => s.running).forEach(s => {
      const option = document.createElement('option');
      option.value = s.station_id;
      option.textContent = s.station_id;
      batterySelect.appendChild(option);
    });
    if (currentBatteryValue && stations.find(s => s.station_id === currentBatteryValue)) {
      batterySelect.value = currentBatteryValue;
    }
  }
  
  // Restore selection if it still exists
  if (select && currentValue && stations.find(s => s.station_id === currentValue)) {
    select.value = currentValue;
  }

  if (securitySelect) {
    const currentSecurityValue = securitySelect.value;
    securitySelect.innerHTML = '<option value="">All stations</option>';
    stations.forEach(s => {
      const option = document.createElement('option');
      option.value = s.station_id;
      option.textContent = s.station_id;
      securitySelect.appendChild(option);
    });
    if (currentSecurityValue && stations.find(s => s.station_id === currentSecurityValue)) {
      securitySelect.value = currentSecurityValue;
    }
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
    const res = await apiFetch(`/stations/${stationId}/test_profiles`, {
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
    const res = await apiFetch(`/stations/${stationId}/charging_profile`, {
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
    const res = await apiFetch(`/stations/${stationId}/logs`);
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
    const res = await apiFetch(`/stations/${currentModalStation}/charging_profile`, {
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
    const res = await apiFetch(
      `/stations/${stationId}/composite_schedule?connector_id=1&duration=3600&charging_rate_unit=W`
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

/* =========================================================
   BATTERY PROFILE
   ========================================================= */

function resetBatteryForm() {
  const fields = [
    "battery-capacity",
    "battery-soc",
    "battery-max-kw",
    "battery-temp"
  ];
  fields.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  const taper = document.getElementById("battery-taper");
  if (taper) taper.checked = true;
}

async function applyBatteryProfile() {
  const stationSelect = document.getElementById("battery-station-select");
  if (!stationSelect || !stationSelect.value) {
    showError("Select a station for battery update");
    return;
  }

  const payload = {};
  const capacity = document.getElementById("battery-capacity");
  const soc = document.getElementById("battery-soc");
  const maxKw = document.getElementById("battery-max-kw");
  const temp = document.getElementById("battery-temp");
  const taper = document.getElementById("battery-taper");

  if (capacity && capacity.value !== "") payload.capacity_kwh = parseFloat(capacity.value);
  if (soc && soc.value !== "") payload.soc_kwh = parseFloat(soc.value);
  if (maxKw && maxKw.value !== "") payload.max_charge_power_kw = parseFloat(maxKw.value);
  if (temp && temp.value !== "") payload.temperature_c = parseFloat(temp.value);
  if (taper) payload.tapering_enabled = taper.checked;

  showSpinner(`Updating battery for ${stationSelect.value}…`, "Please wait");
  try {
    const res = await apiFetch(`/stations/${stationSelect.value}/battery_profile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || "Failed to update battery profile");
    }
    showSuccess("Battery profile updated");
  } catch (err) {
    showError(err.message);
  } finally {
    hideSpinner();
  }
}
