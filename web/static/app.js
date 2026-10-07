let state = {
  fileId: null,
  filename: null,
  summary: null,
  analysis: null,
  probeGrid: null,
  heatmapMode: "ir_drop",
  vnom: 1.0,
  limitMv: 50.0,
  currentMa: 400.0,
  gridRes: 100,
  cutlineDebounceTimer: null,

  // Pan & Zoom Engine
  zoomLevel: 1.0,
  panX: 0,
  panY: 0,
  isPanning: false,
  panStartX: 0,
  panStartY: 0,

  // What-If Dynamic Scaler
  whatIfScale: 100,
};

// Initialize drag & drop and default benchmark on load
window.addEventListener("DOMContentLoaded", () => {
  setupDragAndDrop();
  loadSample("mesh_pdn");
});

function setupDragAndDrop() {
  const dropzone = document.getElementById("dropzone");

  ["dragenter", "dragover"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.add("dragover");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.remove("dragover");
    });
  });

  dropzone.addEventListener("drop", (e) => {
    const files = e.dataTransfer.files;
    if (files.length > 0) {
      uploadFile(files[0]);
    }
  });
}

function handleFileSelect(event) {
  const file = event.target.files[0];
  if (file) {
    uploadFile(file);
  }
}

async function uploadFile(file) {
  showLoading("PARSING GDSII GEOMETRY...");
  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch("/api/upload", {
      method: "POST",
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Upload failed");
    }
    const data = await res.json();
    onFileLoaded(data);
    hideLoading();
    runAnalysis();
  } catch (err) {
    hideLoading();
    alert("Error uploading file: " + err.message);
  }
}

async function loadSample(sampleId) {
  showLoading(`LOADING BENCHMARK [${sampleId}]...`);
  try {
    const res = await fetch(`/api/load-sample/${sampleId}`, { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Sample load failed");
    }
    const data = await res.json();
    onFileLoaded(data);
    hideLoading();
    runAnalysis();
  } catch (err) {
    hideLoading();
    alert("Error loading sample: " + err.message);
  }
}

function onFileLoaded(data) {
  state.fileId = data.file_id;
  state.filename = data.filename;
  state.summary = data.summary;

  document.getElementById("dropzoneText").innerHTML = `<b>LOADED:</b> ${data.filename}`;
  document.getElementById("topCellName").innerText = data.summary.top_cell;

  const bb = data.summary.bbox_microns;
  document.getElementById("dieDimensions").innerText = `${bb.width.toFixed(1)} × ${bb.height.toFixed(1)} µm`;
  document.getElementById("dieBbox").innerText = `[${bb.min_x}, ${bb.min_y}] to [${bb.max_x}, ${bb.max_y}]`;
  document.getElementById("layerCount").innerText = `${data.summary.layers.length} detected`;

  document.getElementById("layoutSummaryCard").style.display = "block";
  document.getElementById("layerStackCard").style.display = "block";

  // Configure cutline slider limits
  const cutlineSlider = document.getElementById("cutlineYSlider");
  cutlineSlider.min = bb.min_y;
  cutlineSlider.max = bb.max_y;
  cutlineSlider.value = (bb.min_y + bb.height * 0.5).toFixed(1);
  if (data.tech && document.getElementById("techSelect")) {
    document.getElementById("techSelect").value = data.tech;
  }

  populateLayerTable(data.summary.layers);
  resetZoom();
}

function onTechChanged() {
  if (state.fileId) {
    runAnalysis();
  }
}

function populateLayerTable(layers) {
  const tbody = document.getElementById("layerTableBody");
  tbody.innerHTML = "";

  layers.forEach((l) => {
    const tr = document.createElement("tr");

    const tdName = document.createElement("td");
    tdName.innerHTML = `<b>L${l.layer}</b> <span style="color:var(--text-muted);font-size:0.68rem;">(${l.name})</span>`;

    const tdRole = document.createElement("td");
    const roleSelect = document.createElement("select");
    roleSelect.id = `layer_role_${l.layer}`;
    ["metal", "via", "pad", "ignore"].forEach((r) => {
      const opt = document.createElement("option");
      opt.value = r;
      opt.innerText = r.toUpperCase();
      if (r === l.role) opt.selected = true;
      roleSelect.appendChild(opt);
    });
    tdRole.appendChild(roleSelect);

    const tdRes = document.createElement("td");
    const resInput = document.createElement("input");
    resInput.type = "number";
    resInput.step = "0.01";
    resInput.min = "0.001";
    resInput.id = `layer_res_${l.layer}`;
    resInput.value = l.role === "via" ? l.via_resistance : l.sheet_resistance;
    tdRes.appendChild(resInput);

    tr.appendChild(tdName);
    tr.appendChild(tdRole);
    tr.appendChild(tdRes);
    tbody.appendChild(tr);
  });
}

function getLayerOverrides() {
  if (!state.summary) return {};
  const overrides = {};
  state.summary.layers.forEach((l) => {
    const roleEl = document.getElementById(`layer_role_${l.layer}`);
    const resEl = document.getElementById(`layer_res_${l.layer}`);
    if (roleEl && resEl) {
      const role = roleEl.value;
      const val = parseFloat(resEl.value);
      overrides[l.layer] = {
        role: role,
        sheet_res: role === "metal" ? val : l.sheet_resistance,
        via_res: role === "via" ? val : l.via_resistance,
      };
    }
  });
  return overrides;
}

// Electrical Parameters Handlers
function updateVnom(val) {
  state.vnom = parseFloat(val) || 1.0;
  document.getElementById("vnomValDisplay").innerText = `${state.vnom.toFixed(2)} V`;
  updateLimit(document.getElementById("limitInput").value);
}

function setVnom(val) {
  document.getElementById("vnomInput").value = val.toFixed(2);
  updateVnom(val);
}

function updateLimit(val) {
  state.limitMv = parseFloat(val) || 50.0;
  const pct = ((state.limitMv / (state.vnom * 1000.0)) * 100.0).toFixed(1);
  document.getElementById("limitValDisplay").innerText = `${state.limitMv.toFixed(1)} mV (${pct}%)`;
}

function setLimit(val) {
  document.getElementById("limitInput").value = val.toFixed(1);
  updateLimit(val);
}

function updateCurrent(val) {
  state.currentMa = parseFloat(val) || 400.0;
  document.getElementById("currentValDisplay").innerText = `${state.currentMa.toFixed(0)} mA`;
}

function updateGridRes(val) {
  state.gridRes = parseInt(val) || 100;
  document.getElementById("gridResDisplay").innerText = `${state.gridRes} × ${state.gridRes}`;
}

// View Modes
function setHeatmapMode(mode) {
  state.heatmapMode = mode;
  document.querySelectorAll(".btn-toggle").forEach((btn) => btn.classList.remove("active"));
  const btnMap = {
    ir_drop: "btnModeDrop",
    voltage: "btnModeVolt",
    margin_slack: "btnModeSlack",
    "3d_surface": "btnMode3D",
    current_flow: "btnModeFlow",
    histogram: "btnModeHist",
  };
  const activeBtn = document.getElementById(btnMap[mode]);
  if (activeBtn) activeBtn.classList.add("active");

  // Show cutline laser only in 2D layout views
  const laser = document.getElementById("cutlineLaserLine");
  if (laser) {
    laser.style.display = (mode === "ir_drop" || mode === "voltage" || mode === "margin_slack") ? "block" : "none";
  }

  refreshView();
}

function refreshView() {
  if (state.fileId) {
    runAnalysis();
  }
}

// Execute Analysis
async function runAnalysis() {
  if (!state.fileId) {
    alert("Please upload a GDSII file or select a benchmark sample first.");
    return;
  }

  showLoading("SOLVING PDN SPARSE RESISTIVE SYSTEM...");
  document.getElementById("runBtn").disabled = true;

  const payload = {
    file_id: state.fileId,
    v_nom: parseFloat(document.getElementById("vnomInput").value),
    limit_mv: parseFloat(document.getElementById("limitInput").value),
    total_current: parseFloat(document.getElementById("currentInput").value) / 1000.0,
    distribution: document.getElementById("distSelect").value,
    grid_resolution: parseInt(document.getElementById("gridResInput").value),
    tech: document.getElementById("techSelect") ? document.getElementById("techSelect").value : "default",
    layer_overrides: getLayerOverrides(),
    heatmap_mode: state.heatmapMode,
    show_layout_overlay: document.getElementById("chkOverlay").checked,
    show_contours: document.getElementById("chkContours").checked,
    show_pads: document.getElementById("chkPads").checked,
    show_worst_marker: document.getElementById("chkWorst").checked,
    cmap_name: document.getElementById("cmapSelect").value,
  };

  try {
    const res = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Analysis failed");
    }

    const data = await res.json();
    renderAnalysisResults(data);
    hideLoading();
  } catch (err) {
    hideLoading();
    alert("Simulation Error: " + err.message);
  } finally {
    document.getElementById("runBtn").disabled = false;
  }
}

function renderAnalysisResults(data) {
  state.analysis = data.analysis;
  state.probeGrid = data.probe_grid;

  const a = data.analysis;

  // 1. Precise Value Margin Banner
  const banner = document.getElementById("signoffBanner");
  const badge = document.getElementById("statusBadge");
  const statusTxt = document.getElementById("statusText");
  const marginEl = document.getElementById("marginVal");

  banner.className = `signoff-banner ${a.is_safe ? "pass" : "violation"}`;
  badge.className = `status-pill ${a.is_safe ? "pass" : "violation"}`;
  if (statusTxt) statusTxt.innerText = a.is_safe ? "PASS (SAFE)" : "VIOLATION";

  const verdictEl = document.getElementById("signoffVerdictText");
  if (verdictEl) {
    verdictEl.innerText = a.is_safe
      ? "DESIGN MEETS ALL ESTIMATION MARGIN RULES"
      : "IR-DROP EXCEEDS MARGIN LIMIT! TIMING RISKS DETECTED";
    verdictEl.style.color = a.is_safe ? "var(--pass-green)" : "var(--fail-red)";
  }

  const marginSign = a.margin_mv >= 0 ? "+" : "";
  marginEl.innerText = `${marginSign}${a.margin_mv.toFixed(2)} mV`;
  marginEl.className = `metric-val ${a.is_safe ? "pass" : "violation"}`;
  document.getElementById("marginPctSub").innerText = `Slack: ${a.margin_percentage >= 0 ? "+" : ""}${a.margin_percentage.toFixed(1)}%`;

  document.getElementById("maxDropVal").innerText = `${a.delta_v_max_mv.toFixed(2)} mV`;
  document.getElementById("dropLimitSub").innerText = `Limit: ${a.delta_v_limit_mv.toFixed(1)} mV`;

  document.getElementById("minVoltageVal").innerText = `${a.min_observed_voltage_v.toFixed(4)} V`;
  document.getElementById("minAllowedSub").innerText = `Allowed: ${a.min_allowed_voltage_v.toFixed(4)} V`;

  // Safe Current & Power Budget
  const safeCurrentEl = document.getElementById("safeCurrentVal");
  if (safeCurrentEl) {
    safeCurrentEl.innerText = `${a.max_safe_current_ma.toFixed(1)} mA`;
    const hdSign = a.current_headroom_ma >= 0 ? "+" : "";
    document.getElementById("currentHeadroomSub").innerText = `Headroom: ${hdSign}${a.current_headroom_ma.toFixed(1)} mA`;
    safeCurrentEl.className = `metric-val ${a.current_headroom_ma >= 0 ? "pass" : "violation"}`;
  }

  const safePowerEl = document.getElementById("safePowerVal");
  if (safePowerEl) {
    safePowerEl.innerText = `${a.max_safe_power_w.toFixed(3)} W`;
    document.getElementById("currentPowerSub").innerText = `Operating: ${a.current_power_w.toFixed(3)} W`;
  }

  const effResEl = document.getElementById("effResVal");
  if (effResEl) {
    effResEl.innerText = `${a.effective_pdn_resistance_ohm.toFixed(4)} Ω`;
    document.getElementById("peakResSub").innerText = `Peak: ${a.peak_pdn_resistance_ohm.toFixed(4)} Ω`;
  }

  // Budget Progress Meter Bar
  const operatingMa = state.currentMa || 400.0;
  const budgetMa = a.max_safe_current_ma || 1.0;
  const ratio = (operatingMa / Math.max(budgetMa, 0.01)) * 100.0;

  const fillEl = document.getElementById("budgetMeterFill");
  const ratioEl = document.getElementById("budgetMeterRatio");

  if (fillEl && ratioEl) {
    fillEl.style.width = `${Math.min(100, ratio)}%`;
    ratioEl.innerText = `Operating: ${operatingMa.toFixed(0)} mA / Safe Limit: ${budgetMa.toFixed(1)} mA (${ratio.toFixed(1)}% utilized)`;
    fillEl.className = "budget-meter-fill " + (ratio <= 80 ? "safe" : (ratio <= 100 ? "warning" : "violation"));
  }

  // Activate What-If Panel
  const whatIfPanel = document.getElementById("whatIfPanel");
  if (whatIfPanel) {
    whatIfPanel.style.display = "flex";
    onWhatIfSlide(100);
  }

  // 2. Heatmap Image Display
  const imgEl = document.getElementById("heatmapImage");
  imgEl.src = data.heatmap_image;
  imgEl.style.display = "block";
  document.getElementById("stagePlaceholder").style.display = "none";

  // 3. Cutline Image
  const cutImg = document.getElementById("cutlineImage");
  cutImg.src = data.cutline_image;

  // 4. Hotspots Table
  renderHotspotsTable(a.hotspots);

  // 5. Layer Stats Table
  renderLayerStatsTable(a.layer_metrics);

  // 6. Statistics Tab
  document.getElementById("statMean").innerText = `${a.delta_v_avg_mv.toFixed(2)} mV`;
  document.getElementById("statStd").innerText = `${a.delta_v_std_mv.toFixed(2)} mV`;
  document.getElementById("statP90").innerText = `${a.p90_drop_mv.toFixed(2)} mV`;
  document.getElementById("statP95").innerText = `${a.p95_drop_mv.toFixed(2)} mV`;
  document.getElementById("statP99").innerText = `${a.p99_drop_mv.toFixed(2)} mV`;

  const sCurr = document.getElementById("statSafeCurr");
  if (sCurr) sCurr.innerText = `${a.max_safe_current_ma.toFixed(1)} mA`;
  const sHead = document.getElementById("statHeadroom");
  if (sHead) sHead.innerText = `${a.current_headroom_ma >= 0 ? "+" : ""}${a.current_headroom_ma.toFixed(1)} mA`;
  const sPow = document.getElementById("statSafePower");
  if (sPow) sPow.innerText = `${a.max_safe_power_w.toFixed(3)} W`;
  const sEff = document.getElementById("statEffRes");
  if (sEff) sEff.innerText = `${a.effective_pdn_resistance_ohm.toFixed(4)} Ω`;
  const sPeak = document.getElementById("statPeakRes");
  if (sPeak) sPeak.innerText = `${a.peak_pdn_resistance_ohm.toFixed(4)} Ω`;
}

function renderHotspotsTable(hotspots) {
  const tbody = document.getElementById("hotspotsTableBody");
  tbody.innerHTML = "";
  if (!hotspots || hotspots.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--pass-green); font-weight:bold;">No IR-drop violations detected! All nodes within safe margin.</td></tr>`;
    return;
  }

  hotspots.forEach((h) => {
    const tr = document.createElement("tr");
    tr.style.cursor = "pointer";
    tr.title = "Click to focus on this hotspot";
    tr.onclick = () => focusHotspot(h);

    tr.innerHTML = `
      <td><b>#${h.id}</b></td>
      <td>(${h.center_um[0].toFixed(1)}, ${h.center_um[1].toFixed(1)}) µm</td>
      <td>[${h.bbox_um[0]}, ${h.bbox_um[1]}] to [${h.bbox_um[2]}, ${h.bbox_um[3]}]</td>
      <td>${h.area_um2.toFixed(1)} µm²</td>
      <td style="color:var(--fail-red); font-weight:bold;">${h.max_drop_mv.toFixed(2)} mV</td>
      <td><span class="badge violation">+${h.violation_severity_mv.toFixed(1)} mV</span></td>
    `;
    tbody.appendChild(tr);
  });
}

function focusHotspot(h) {
  // Smoothly zoom in and center on the hotspot
  state.zoomLevel = 2.2;
  updateTransform();
}

function renderLayerStatsTable(layerMetrics) {
  const tbody = document.getElementById("layerStatsTableBody");
  tbody.innerHTML = "";
  if (!layerMetrics || layerMetrics.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;">No layer metrics available.</td></tr>`;
    return;
  }

  layerMetrics.forEach((lm) => {
    const tr = document.createElement("tr");
    const isPass = lm.margin_mv >= 0;
    tr.innerHTML = `
      <td><b>Layer ${lm.layer}</b></td>
      <td>${lm.max_drop_mv.toFixed(2)}</td>
      <td>${lm.avg_drop_mv.toFixed(2)}</td>
      <td style="color:${isPass ? "var(--pass-green)" : "var(--fail-red)"}; font-weight:bold;">${lm.margin_mv >= 0 ? "+" : ""}${lm.margin_mv.toFixed(2)}</td>
      <td><span class="badge ${isPass ? "pass" : "violation"}">${lm.status}</span></td>
    `;
    tbody.appendChild(tr);
  });
}

// Pan and zoom
function updateTransform() {
  const wrapper = document.getElementById("heatmapCanvasWrapper");
  if (wrapper) {
    wrapper.style.transform = `translate(${state.panX}px, ${state.panY}px) scale(${state.zoomLevel})`;
  }
  const zoomDisplay = document.getElementById("zoomLevelDisplay");
  if (zoomDisplay) {
    zoomDisplay.innerText = `${Math.round(state.zoomLevel * 100)}%`;
  }
}

function zoomIn() {
  state.zoomLevel = Math.min(6.0, state.zoomLevel * 1.25);
  updateTransform();
}

function zoomOut() {
  state.zoomLevel = Math.max(0.6, state.zoomLevel / 1.25);
  updateTransform();
}

function resetZoom() {
  state.zoomLevel = 1.0;
  state.panX = 0;
  state.panY = 0;
  updateTransform();
}

function fitZoom() {
  state.zoomLevel = 1.0;
  state.panX = 0;
  state.panY = 0;
  updateTransform();
}

function startPan(e) {
  if (e.button !== 0) return; // Only left click
  state.isPanning = true;
  state.panStartX = e.clientX - state.panX;
  state.panStartY = e.clientY - state.panY;
  document.getElementById("heatmapViewport").classList.add("panning");
}

function onViewportMouseMove(e) {
  if (state.isPanning) {
    state.panX = e.clientX - state.panStartX;
    state.panY = e.clientY - state.panStartY;
    updateTransform();
  } else {
    handleProbe(e);
  }
}

function endPan(e) {
  state.isPanning = false;
  const vp = document.getElementById("heatmapViewport");
  if (vp) vp.classList.remove("panning");
}

function onViewportWheel(e) {
  e.preventDefault();
  const delta = e.deltaY < 0 ? 1.15 : 0.85;
  state.zoomLevel = Math.max(0.5, Math.min(6.0, state.zoomLevel * delta));
  updateTransform();
}

// Cursor probe
function handleProbe(e) {
  const img = document.getElementById("heatmapImage");
  const hud = document.getElementById("probeHud");
  if (!state.probeGrid || !img || img.style.display === "none") {
    if (hud) hud.style.display = "none";
    return;
  }

  const rect = img.getBoundingClientRect();
  const relX = (e.clientX - rect.left) / rect.width;
  const relY = 1.0 - (e.clientY - rect.top) / rect.height;

  if (relX < 0 || relX > 1 || relY < 0 || relY > 1) {
    if (hud) hud.style.display = "none";
    return;
  }

  const grid = state.probeGrid;
  const numX = grid.x.length;
  const numY = grid.y.length;

  const ix = Math.min(Math.floor(relX * numX), numX - 1);
  const iy = Math.min(Math.floor(relY * numY), numY - 1);

  const x_um = grid.x[ix];
  const y_um = grid.y[iy];
  const drop_mv = grid.drop_mv[iy][ix];
  const v_nom = state.analysis ? state.analysis.v_nom : 1.0;
  const limit_mv = state.analysis ? state.analysis.delta_v_limit_mv : 50.0;

  const volt_v = v_nom - drop_mv / 1000.0;
  const slack_mv = limit_mv - drop_mv;

  hud.style.display = "block";
  document.getElementById("probePos").innerText = `(${x_um.toFixed(1)}, ${y_um.toFixed(1)}) µm`;
  document.getElementById("probeDrop").innerText = `${drop_mv.toFixed(2)} mV`;
  document.getElementById("probeVolt").innerText = `${volt_v.toFixed(4)} V`;

  const marginEl = document.getElementById("probeMargin");
  marginEl.innerText = `${slack_mv >= 0 ? "+" : ""}${slack_mv.toFixed(2)} mV`;
  marginEl.style.color = slack_mv >= 0 ? "var(--pass-green)" : "var(--fail-red)";
}

function hideProbe() {
  const hud = document.getElementById("probeHud");
  if (hud) hud.style.display = "none";
}

// Cutline slider
function updateCutlineY(val) {
  const y_um = parseFloat(val);
  document.getElementById("cutlineYVal").innerText = `${y_um.toFixed(1)} µm`;

  clearTimeout(state.cutlineDebounceTimer);
  state.cutlineDebounceTimer = setTimeout(async () => {
    if (!state.fileId) return;
    try {
      const res = await fetch("/api/cutline", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_id: state.fileId, cutline_y_um: y_um }),
      });
      if (res.ok) {
        const data = await res.json();
        document.getElementById("cutlineImage").src = data.cutline_image;
      }
    } catch (e) {
      console.error("Cutline update failed:", e);
    }
  }, 120);
}

// Current scaling sensitivity
function onWhatIfSlide(val) {
  state.whatIfScale = parseInt(val);
  const factor = state.whatIfScale / 100.0;

  document.getElementById("whatIfSlider").value = state.whatIfScale;
  document.getElementById("whatIfScaleVal").innerText = `${state.whatIfScale}%`;

  const nominalCurrMa = parseFloat(document.getElementById("currentInput").value) || 400.0;
  const scaledCurrMa = nominalCurrMa * factor;
  document.getElementById("whatIfCurr").innerText = `${scaledCurrMa.toFixed(0)} mA`;

  if (!state.analysis) return;

  const predDropMv = state.analysis.delta_v_max_mv * factor;
  const predMarginMv = state.analysis.delta_v_limit_mv - predDropMv;

  const dropEl = document.getElementById("whatIfDrop");
  const marginEl = document.getElementById("whatIfMargin");

  dropEl.innerText = `${predDropMv.toFixed(2)} mV`;
  marginEl.innerText = `${predMarginMv >= 0 ? "+" : ""}${predMarginMv.toFixed(2)} mV`;
  marginEl.style.color = predMarginMv >= 0 ? "var(--pass-green)" : "var(--fail-red)";
}

function applyWhatIf() {
  const factor = state.whatIfScale / 100.0;
  const nominalCurrMa = parseFloat(document.getElementById("currentInput").value) || 400.0;
  const newCurr = Math.round(nominalCurrMa * factor);
  document.getElementById("currentInput").value = newCurr;
  updateCurrent(newCurr);
  resetWhatIf();
  runAnalysis();
}

function resetWhatIf() {
  state.whatIfScale = 100;
  onWhatIfSlide(100);
}

// Tab Switching
function switchTab(tabId) {
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));

  event.target.classList.add("active");
  document.getElementById(tabId).classList.add("active");
}

// Exports
function downloadHeatmap() {
  const img = document.getElementById("heatmapImage");
  if (!img || !img.src) {
    alert("Run analysis first!");
    return;
  }
  const a = document.createElement("a");
  a.href = img.src;
  a.download = `${state.filename || "layout"}_irdrop_heatmap.png`;
  a.click();
}

function exportSignoffReport() {
  if (!state.fileId) {
    alert("Run analysis first!");
    return;
  }
  window.open(`/api/export-report/${state.fileId}`, "_blank");
}

function exportCsv() {
  if (!state.fileId) {
    alert("Run analysis first!");
    return;
  }
  window.open(`/api/export-csv/${state.fileId}`, "_blank");
}

function exportHtmlReport() {
  if (!state.fileId) {
    alert("Run analysis first!");
    return;
  }
  window.open(`/api/export-html-report/${state.fileId}`, "_blank");
}

// Loading UI
function showLoading(msg) {
  document.getElementById("loadingText").innerText = msg || "PROCESSING...";
  document.getElementById("loadingOverlay").style.display = "flex";
}

function hideLoading() {
  document.getElementById("loadingOverlay").style.display = "none";
}
