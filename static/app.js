/* =========================================================
   GLOBAL STATE
   ========================================================= */

const API_BASE = window.location.origin;

let cachedStations = [];
let currentPrice = 20;
let basePrice = 20;

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

    const tbody = document.getElementById("stations-body");
    if (!tbody) return;

    tbody.innerHTML = "";

    if (!stations.length) {
      tbody.innerHTML = `<tr><td colspan="7">No stations</td></tr>`;
      return;
    }

    stations.forEach(s => {
      const status = s.running
        ? `<span class="badge badge-online">online</span>`
        : `<span class="badge badge-offline">stopped</span>`;

      const usage = s.running ? `${s.usage_kw.toFixed(2)} kW` : "–";
      const energy = `${s.energy_kwh.toFixed(3)} kWh`;

      const action = s.running
        ? `<button class="btn-danger" onclick="stopStation('${s.station_id}')">Stop</button>`
        : `<button class="btn-primary" onclick="startStation('${s.station_id}')">Start</button>`;

      const logsButton = `<button class="btn-ghost" onclick="toggleLogs('${s.station_id}')">📋 Logs</button>`;

      tbody.innerHTML += `
        <tr>
          <td>${s.station_id}</td>
          <td>${s.profile}</td>
          <td>${status}</td>
          <td>${usage}</td>
          <td>${energy}</td>
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
          <td colspan="7">
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
