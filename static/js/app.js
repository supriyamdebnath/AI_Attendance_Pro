const iconEye = `
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"></path>
    <circle cx="12" cy="12" r="3"></circle>
  </svg>`;
const iconEyeOff = `
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="M3 3l18 18"></path>
    <path d="M10.6 10.6a3 3 0 0 0 4.2 4.2"></path>
    <path d="M9.9 4.4A10.7 10.7 0 0 1 12 4.2c6.5 0 10 7 10 7a18 18 0 0 1-3.1 4.2"></path>
    <path d="M6.1 6.1C3.5 7.9 2 11.2 2 11.2s3.5 7 10 7c1.5 0 2.8-.3 4-.9"></path>
  </svg>`;

let lastSuccessKey = "";
let lastLiveLogKey = "";
let recentRowsSignature = "";
let dashboardRefreshTimer = null;
let liveSocket = null;
let liveSocketRetry = 0;
let fallbackRefreshTimer = null;

const cameraController = {
  stream: null,
  timer: null,
  sending: false,
  lastSentAt: 0,
  fps: 0,
};

function snapshotURL(path) {
  if (!path) return "";
  return path.startsWith("/") ? path : `/${path}`;
}

function setNodeText(node, value) {
  const next = String(value ?? "-");
  if (node && node.textContent !== next) node.textContent = next;
}

function updateCameraStage(state, headline, detail) {
  document.querySelectorAll("[data-camera-stage]").forEach((stage) => {
    const nextClass = state ? `is-${state}` : "";
    ["is-live", "is-loading", "is-error"].forEach((name) => {
      stage.classList.toggle(name, name === nextClass);
    });
    const placeholder = stage.querySelector("[data-camera-placeholder]");
    if (!placeholder) return;
    const strong = placeholder.querySelector("strong");
    const span = placeholder.querySelector("span");
    if (strong && headline) setNodeText(strong, headline);
    if (span && detail) setNodeText(span, detail);
  });
}

function setCameraStatus(message) {
  document.querySelectorAll("[data-camera-status]").forEach((node) => setNodeText(node, message));
}

function updateIdentity(event = {}) {
  document.querySelectorAll("[data-fps]").forEach((node) => setNodeText(node, Math.round(event.fps || 0)));
  document.querySelectorAll("[data-recognition-state]").forEach((node) => setNodeText(node, event.message || "Scanning"));
  document.querySelectorAll("[data-live-name]").forEach((node) => setNodeText(node, event.name || "-"));
  document.querySelectorAll("[data-live-role]").forEach((node) => setNodeText(node, event.role || "-"));
  document.querySelectorAll("[data-live-id]").forEach((node) => setNodeText(node, event.id_number || "-"));
  document.querySelectorAll("[data-live-date]").forEach((node) => setNodeText(node, event.date || "-"));
  document.querySelectorAll("[data-live-time]").forEach((node) => setNodeText(node, event.time || "-"));
  document.querySelectorAll("[data-live-accuracy]").forEach((node) => setNodeText(node, event.recognition_accuracy ?? event.confidence ?? 0));
  document.querySelectorAll("[data-live-status]").forEach((node) => setNodeText(node, event.status || "Waiting"));

  document.querySelectorAll("[data-snapshot-preview]").forEach((img) => {
    const empty = img.parentElement.querySelector("[data-snapshot-empty]");
    if (!event.snapshot_path) return;
    const next = snapshotURL(event.snapshot_path);
    if (img.src !== new URL(next, window.location.origin).href) img.src = next;
    img.classList.add("is-visible");
    if (empty) empty.style.display = "none";
  });

  document.querySelectorAll("[data-live-logs]").forEach((list) => {
    if (!event.message) return;
    const logKey = event.state === "unknown" ? event.message : `${event.message}-${event.time || ""}`;
    if (logKey === lastLiveLogKey) return;
    lastLiveLogKey = logKey;
    const item = document.createElement("div");
    item.className = "activity-item";
    item.innerHTML = `<span>${escapeHTML(event.message)}</span><small>${escapeHTML(event.time || new Date().toLocaleTimeString())}</small>`;
    list.prepend(item);
    while (list.children.length > 6) list.lastElementChild.remove();
  });

  const successKey = `${event.id_number || ""}-${event.date || ""}-${event.time || ""}`;
  if (event.state === "recognized" && event.message === "Attendance Marked Successfully" && successKey !== lastSuccessKey) {
    lastSuccessKey = successKey;
    const pop = document.querySelector("[data-success-pop]");
    if (pop) {
      const detail = pop.querySelector("[data-success-detail]");
      if (detail) setNodeText(detail, `${event.name} - ${event.role} - ${event.time} - ${event.status}`);
      pop.classList.add("is-visible");
      setTimeout(() => pop.classList.remove("is-visible"), 3600);
    }
    notify(`${event.name} marked present`, "success");
    playSuccessTone();
  }
}

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

async function requestJSON(url, options = {}) {
  const headers = { "X-Requested-With": "fetch", ...(options.headers || {}) };
  if (options.body && typeof options.body !== "string") {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(url, { method: "POST", ...options, headers });
  let payload = {};
  try {
    payload = await response.json();
  } catch (_error) {}
  return { ok: response.ok, payload, status: response.status };
}

async function postJSON(url, body = null) {
  const result = await requestJSON(url, body ? { body } : {});
  notify(result.payload.message || "Done", result.ok ? "success" : "danger");
  return result;
}

function setNotificationBadge(value) {
  document.querySelectorAll("[data-notification-badge]").forEach((badge) => {
    const next = Number(value || 0);
    badge.textContent = next > 99 ? "99+" : String(next);
    badge.classList.toggle("is-empty", next <= 0);
  });
}

function incrementNotification(message) {
  document.querySelectorAll("[data-notification-badge]").forEach((badge) => {
    const raw = badge.textContent === "99+" ? 99 : Number(badge.textContent || 0);
    const next = raw >= 99 ? 1 : raw + 1;
    badge.textContent = String(next);
    badge.classList.toggle("is-empty", next <= 0);
  });
  document.querySelectorAll("[data-notification-list]").forEach((list) => {
    const empty = list.querySelector("p");
    if (empty) empty.remove();
    const item = document.createElement("div");
    item.className = "activity-item";
    item.innerHTML = `<span>${escapeHTML(message)}</span><small>${new Date().toLocaleTimeString()}</small>`;
    list.prepend(item);
    while (list.children.length > 20) list.lastElementChild.remove();
  });
}

function assignMobileTableLabels(root = document) {
  root.querySelectorAll("table").forEach((table) => {
    const headers = [...table.querySelectorAll("thead th")].map((header) => header.textContent.trim());
    table.querySelectorAll("tbody tr").forEach((row) => {
      [...row.children].forEach((cell, index) => {
        if (!cell.hasAttribute("data-label") && headers[index]) {
          cell.setAttribute("data-label", headers[index]);
        }
      });
    });
  });
}

function setMobileNav(open) {
  document.documentElement.classList.toggle("mobile-nav-open", open);
  document.body.classList.toggle("mobile-nav-open", open);
  document.querySelectorAll("[data-mobile-menu]").forEach((button) => {
    button.setAttribute("aria-expanded", open ? "true" : "false");
  });
}

function openNotificationsPanel() {
  const button = document.querySelector("[data-notification-toggle]");
  const panel = document.querySelector("[data-notification-panel]");
  if (!button || !panel) return;
  panel.classList.add("is-open");
  button.setAttribute("aria-expanded", "true");
  document.querySelector("[data-profile-panel]")?.classList.remove("is-open");
}

function renderGlobalSearch(items = []) {
  const results = document.querySelector("[data-global-search-results]");
  if (!results) return;
  if (!items.length) {
    results.innerHTML = `<p class="muted mb-0">No results found.</p>`;
    results.classList.add("is-open");
    return;
  }
  results.innerHTML = items.map((item) => `<a class="global-search-item" href="${escapeHTML(item.url)}"><span>${escapeHTML(item.type)}</span><strong>${escapeHTML(item.title)}</strong><small>${escapeHTML(item.detail)}</small></a>`).join("");
  results.classList.add("is-open");
}

function notify(message, type = "info") {
  const box = document.createElement("div");
  box.className = `alert alert-${type} glass toastish`;
  box.textContent = message;
  document.body.appendChild(box);
  incrementNotification(message);
  setTimeout(() => box.remove(), 3200);
}

function playSuccessTone() {
  try {
    const audio = new (window.AudioContext || window.webkitAudioContext)();
    const oscillator = audio.createOscillator();
    const gain = audio.createGain();
    oscillator.type = "sine";
    oscillator.frequency.value = 740;
    gain.gain.setValueAtTime(0.0001, audio.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.08, audio.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + 0.22);
    oscillator.connect(gain).connect(audio.destination);
    oscillator.start();
    oscillator.stop(audio.currentTime + 0.24);
  } catch (_error) {}
}

function cameraVideos() {
  return [...document.querySelectorAll("[data-browser-camera]")];
}

async function ensureBrowserCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("Browser camera access requires HTTPS or localhost.");
  }
  if (!cameraController.stream) {
    cameraController.stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
  }
  cameraVideos().forEach((video) => {
    if (video.srcObject !== cameraController.stream) video.srcObject = cameraController.stream;
    video.play().catch(() => {});
  });
  updateCameraStage("live", "Browser camera live", "Frames are securely sampled for recognition.");
  setCameraStatus("Browser camera active");
  return cameraController.stream;
}

function stopBrowserCamera() {
  if (cameraController.timer) clearInterval(cameraController.timer);
  cameraController.timer = null;
  cameraController.sending = false;
  if (cameraController.stream) {
    cameraController.stream.getTracks().forEach((track) => track.stop());
  }
  cameraController.stream = null;
  cameraVideos().forEach((video) => {
    video.pause();
    video.removeAttribute("srcObject");
    video.srcObject = null;
  });
}

function grabCameraFrame(videoOverride = null, quality = 0.78) {
  const video = videoOverride || cameraVideos().find((node) => node.readyState >= 2);
  if (!video || !video.videoWidth || !video.videoHeight) return "";
  const canvas = document.querySelector("[data-camera-canvas]") || document.createElement("canvas");
  const width = 720;
  const height = Math.round((video.videoHeight / video.videoWidth) * width);
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d", { alpha: false });
  context.drawImage(video, 0, 0, width, height);
  return canvas.toDataURL("image/jpeg", quality);
}

async function sendCameraFrame() {
  if (cameraController.sending || document.hidden) return;
  const image = grabCameraFrame();
  if (!image) return;
  const now = performance.now();
  cameraController.fps = cameraController.lastSentAt ? 1000 / Math.max(now - cameraController.lastSentAt, 1) : 0;
  cameraController.lastSentAt = now;
  cameraController.sending = true;
  try {
    const result = await requestJSON("/api/camera/frame", { body: { image, fps: cameraController.fps } });
    if (result.ok && result.payload.event) {
      updateIdentity(result.payload.event);
      setCameraStatus(result.payload.event.message || result.payload.event.camera);
    }
  } catch (_error) {
    setCameraStatus("Recognition connection interrupted");
  } finally {
    cameraController.sending = false;
  }
}

async function startCameraSession() {
  updateCameraStage("loading", "Opening browser camera", "Allow camera access in your browser.");
  setCameraStatus("Requesting browser camera...");
  try {
    await ensureBrowserCamera();
    const result = await postJSON("/camera/start");
    if (!result.ok) throw new Error(result.payload.message || "Camera failed to start.");
    if (cameraController.timer) clearInterval(cameraController.timer);
    cameraController.timer = setInterval(sendCameraFrame, 650);
    sendCameraFrame();
    connectLiveSocket();
  } catch (error) {
    stopBrowserCamera();
    await requestJSON("/camera/stop");
    updateCameraStage("error", "Camera unavailable", error.message || "Browser camera permission was blocked.");
    setCameraStatus(error.message || "Camera unavailable");
    notify(error.message || "Camera unavailable", "danger");
  }
}

async function stopCameraSession() {
  stopBrowserCamera();
  await postJSON("/camera/stop");
  updateCameraStage("", "Live recognition feed", "Press Camera ON to start the browser camera stream.");
  setCameraStatus("Camera idle");
}

function scheduleDashboardRefresh() {
  clearTimeout(dashboardRefreshTimer);
  dashboardRefreshTimer = setTimeout(refreshDashboard, 350);
}

function currentDashboardPeriod() {
  return document.querySelector("[data-dashboard-period]")?.value || "daily";
}

function renderRecentRows(rows = []) {
  const tbody = document.querySelector("[data-recent-rows]");
  if (!tbody) return;
  const signature = JSON.stringify(rows.map((row) => [row.id_number, row.date, row.time, row.status, row.snapshot_path]));
  if (signature === recentRowsSignature) return;
  recentRowsSignature = signature;
  tbody.innerHTML = rows.length
    ? rows.map((row) => `<tr data-role="${escapeHTML(row.role)}" data-status="${escapeHTML(String(row.status).toLowerCase())}" data-date="${escapeHTML(row.date)}"><td>${escapeHTML(row.id_number)}</td><td>${escapeHTML(row.name)}</td><td>${escapeHTML(row.role)}</td><td>${escapeHTML(row.date)}</td><td>${escapeHTML(row.time)}</td><td><span class="badge-soft">${escapeHTML(row.status)}</span></td><td>${escapeHTML(row.accuracy || 0)}%</td><td>${row.snapshot_path ? `<a class="snapshot-link" href="/${escapeHTML(row.snapshot_path)}" target="_blank">Preview</a>` : `<span class="muted">Pending</span>`}</td></tr>`).join("")
    : `<tr><td colspan="8" class="muted">No attendance yet.</td></tr>`;
  const table = tbody.closest("table");
  if (table) {
    assignMobileTableLabels(table);
    applyTableFilters(table);
  }
}

function applyDashboardPayload(payload = {}) {
  if (payload.metrics) {
    document.querySelectorAll("[data-metric-users]").forEach((node) => setNodeText(node, payload.metrics.users));
    document.querySelectorAll("[data-metric-today]").forEach((node) => setNodeText(node, payload.metrics.today_present));
    document.querySelectorAll("[data-metric-attendance-rate]").forEach((node) => setNodeText(node, `${payload.metrics.attendance_rate}%`));
    document.querySelectorAll("[data-metric-late]").forEach((node) => setNodeText(node, payload.metrics.late_checkins));
    document.querySelectorAll("[data-metric-success]").forEach((node) => setNodeText(node, `${payload.metrics.success_rate}%`));
    document.querySelectorAll("[data-metric-model]").forEach((node) => setNodeText(node, payload.metrics.trained ? "Ready" : "Pending"));
    document.querySelectorAll("[data-metric-camera]").forEach((node) => setNodeText(node, payload.metrics.camera));
  }
  if (payload.recent) renderRecentRows(payload.recent);
  if (payload.insights) {
    document.querySelectorAll("[data-ai-insights]").forEach((list) => {
      const signature = JSON.stringify(payload.insights);
      if (list.dataset.signature === signature) return;
      list.dataset.signature = signature;
      list.innerHTML = payload.insights.map((item) => `<div class="insight-item"><strong>${escapeHTML(item.title)}</strong><span>${escapeHTML(item.detail)}</span></div>`).join("");
    });
  }
  if (typeof updateAttendanceCharts === "function") updateAttendanceCharts(payload);
}

async function refreshDashboard() {
  if (!document.querySelector("[data-metric-users]")) return;
  try {
    const response = await fetch(`/api/dashboard/summary?period=${encodeURIComponent(currentDashboardPeriod())}`, { headers: { "X-Requested-With": "fetch" } });
    if (!response.ok) return;
    applyDashboardPayload(await response.json());
  } catch (_error) {}
}

function connectLiveSocket() {
  if (!document.querySelector("[data-camera-start], [data-metric-users]")) return;
  if (!("WebSocket" in window)) {
    if (!fallbackRefreshTimer) fallbackRefreshTimer = setInterval(() => !document.hidden && refreshDashboard(), 10000);
    return;
  }
  if (liveSocket && liveSocket.readyState <= WebSocket.OPEN) return;
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  liveSocket = new WebSocket(`${scheme}://${window.location.host}/ws/live`);
  liveSocket.addEventListener("open", () => { liveSocketRetry = 0; });
  liveSocket.addEventListener("message", (message) => {
    try {
      const payload = JSON.parse(message.data);
      if (payload.event) {
        setCameraStatus(payload.event.message || payload.event.camera);
        updateIdentity(payload.event);
      }
      if (payload.summary) applyDashboardPayload(payload.summary);
    } catch (_error) {}
  });
  liveSocket.addEventListener("close", () => {
    liveSocket = null;
    if (!document.querySelector("[data-camera-start], [data-metric-users]")) return;
    const delay = Math.min(12000, 1200 * 2 ** liveSocketRetry);
    liveSocketRetry += 1;
    setTimeout(connectLiveSocket, delay);
  });
}

function updateNoResults(table) {
  const rows = [...table.querySelectorAll("tbody tr")].filter((row) => !row.querySelector("[colspan]"));
  const visible = rows.some((row) => row.style.display !== "none");
  document.querySelectorAll(`[data-no-results-for="#${table.id}"]`).forEach((node) => {
    node.classList.toggle("is-visible", !visible && rows.length > 0);
  });
}

function applyTableFilters(table) {
  const textInput = document.querySelector(`[data-table-filter][data-target="#${table.id}"]`);
  const strip = document.querySelector(`[data-filter-for="#${table.id}"]`);
  const active = strip?.querySelector("[data-filter-value].active")?.dataset.filterValue || "";
  const dateFilter = strip?.querySelector("[data-date-filter]")?.value || "";
  const query = (textInput?.value || "").toLowerCase();
  table.querySelectorAll("tbody tr").forEach((row) => {
    if (row.querySelector("[colspan]")) return;
    const textMatch = row.textContent.toLowerCase().includes(query);
    const activeMatch = !active || row.dataset.role === active || row.dataset.status === active || row.textContent.toLowerCase().includes(active);
    const dateMatch = !dateFilter || row.dataset.date === dateFilter;
    row.style.display = textMatch && activeMatch && dateMatch ? "" : "none";
  });
  updateNoResults(table);
}

function createCaptureModal(name) {
  const modal = document.createElement("div");
  modal.className = "capture-modal";
  modal.innerHTML = `
    <div class="capture-dialog glass">
      <div class="d-flex justify-content-between align-items-center gap-3 mb-3">
        <div>
          <p class="eyebrow mb-1">Browser face enrollment</p>
          <h2 class="section-title mb-0">${escapeHTML(name)}</h2>
        </div>
        <button class="btn btn-sm btn-outline-light" type="button" data-capture-close>Close</button>
      </div>
      <div class="capture-preview"><video autoplay playsinline muted></video></div>
      <div class="capture-progress"><span></span></div>
      <p class="muted mb-0" data-capture-status>Preparing browser camera...</p>
    </div>`;
  document.body.appendChild(modal);
  return modal;
}

async function captureUserSamples(button) {
  const userId = button.dataset.captureUser;
  const modal = createCaptureModal(button.dataset.captureName || "Face enrollment");
  const video = modal.querySelector("video");
  const progress = modal.querySelector(".capture-progress span");
  const status = modal.querySelector("[data-capture-status]");
  let cancelled = false;
  modal.querySelector("[data-capture-close]").addEventListener("click", () => {
    cancelled = true;
    modal.remove();
    if (!cameraController.timer && !document.querySelector("[data-camera-stage]")) stopBrowserCamera();
  });
  try {
    await ensureBrowserCamera();
    video.srcObject = cameraController.stream;
    await video.play();
    for (let attempt = 0; attempt < 90 && !cancelled; attempt += 1) {
      const image = grabCameraFrame(video, 0.82);
      if (!image) {
        await new Promise((resolve) => setTimeout(resolve, 180));
        continue;
      }
      const result = await requestJSON(`/api/users/${userId}/face-sample`, { body: { image } });
      if (result.ok) {
        const captured = Number(result.payload.captured || 0);
        progress.style.width = `${Math.min(captured / 40, 1) * 100}%`;
        setNodeText(status, result.payload.message);
        if (result.payload.complete) break;
      } else {
        setNodeText(status, result.payload.message || "Adjust face position.");
      }
      await new Promise((resolve) => setTimeout(resolve, 240));
    }
    if (!cancelled) {
      const train = await postJSON("/train");
      setNodeText(status, train.ok ? "Samples captured and model trained." : "Samples captured. Train model after adding more faces.");
      setTimeout(() => modal.remove(), 1400);
    }
  } catch (error) {
    setNodeText(status, error.message || "Camera capture failed.");
    notify(error.message || "Camera capture failed.", "danger");
  } finally {
    if (!cameraController.timer && !document.querySelector("[data-camera-stage]")) stopBrowserCamera();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.body.classList.add("page-ready");
  assignMobileTableLabels();
  document.querySelectorAll("[data-mobile-menu]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      setMobileNav(!document.body.classList.contains("mobile-nav-open"));
    });
  });
  document.querySelectorAll(".sidebar .nav-link-custom").forEach((link) => {
    link.addEventListener("click", () => setMobileNav(false));
  });
  document.querySelectorAll("[data-open-notifications]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      setMobileNav(false);
      openNotificationsPanel();
    });
  });
  document.querySelectorAll(".app-shell").forEach((shell) => {
    shell.addEventListener("click", (event) => {
      if (document.body.classList.contains("mobile-nav-open") && !event.target.closest(".sidebar") && !event.target.closest("[data-mobile-menu]")) {
        setMobileNav(false);
      }
    });
  });
  document.querySelectorAll("[data-notification-badge]").forEach((badge) => {
    badge.classList.toggle("is-empty", badge.textContent === "0");
  });
  document.querySelectorAll("[data-count-to]").forEach((node) => {
    const target = Number(node.dataset.countTo || node.textContent || 0);
    const start = performance.now();
    const step = (time) => {
      const progress = Math.min((time - start) / 900, 1);
      setNodeText(node, Math.round(target * progress));
      if (progress < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
  document.querySelectorAll("[data-dashboard-period]").forEach((select) => {
    select.addEventListener("change", () => {
      const url = new URL(window.location.href);
      url.searchParams.set("period", select.value);
      if (window.location.pathname === "/admin") {
        window.history.replaceState({}, "", url);
      }
      scheduleDashboardRefresh();
    });
  });
  document.querySelectorAll("[data-global-search]").forEach((input) => {
    let searchTimer = null;
    input.addEventListener("input", () => {
      clearTimeout(searchTimer);
      const query = input.value.trim();
      const results = document.querySelector("[data-global-search-results]");
      if (query.length < 2) {
        results?.classList.remove("is-open");
        if (results) results.innerHTML = "";
        return;
      }
      searchTimer = setTimeout(async () => {
        try {
          const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`, { headers: { "X-Requested-With": "fetch" } });
          if (response.ok) renderGlobalSearch((await response.json()).items || []);
        } catch (_error) {}
      }, 180);
    });
  });
  document.querySelectorAll("[data-toggle-password]").forEach((button) => {
    button.innerHTML = iconEye;
    button.addEventListener("click", () => {
      const input = button.parentElement.querySelector("input");
      if (!input) return;
      const hidden = input.type === "password";
      input.type = hidden ? "text" : "password";
      button.innerHTML = hidden ? iconEyeOff : iconEye;
      button.setAttribute("aria-label", hidden ? "Hide password" : "Show password");
      button.setAttribute("title", hidden ? "Hide password" : "Show password");
    });
  });
  document.querySelectorAll("[data-camera-start]").forEach((button) => button.addEventListener("click", startCameraSession));
  document.querySelectorAll("[data-camera-stop]").forEach((button) => button.addEventListener("click", stopCameraSession));
  document.querySelectorAll("[data-snapshot]").forEach((button) => button.addEventListener("click", async () => {
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "Capturing...";
    const image = grabCameraFrame();
    const result = await postJSON("/camera/snapshot", { image });
    if (result.ok && result.payload.snapshot_path) updateIdentity(result.payload.event || { snapshot_path: result.payload.snapshot_path, message: "Snapshot captured" });
    button.disabled = false;
    button.textContent = original;
  }));
  document.querySelectorAll("[data-retake-snapshot]").forEach((button) => button.addEventListener("click", async () => {
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "Retaking...";
    const image = grabCameraFrame();
    const result = await postJSON("/camera/snapshot?retake=1", { image, retake: true });
    if (result.ok && result.payload.snapshot_path) updateIdentity(result.payload.event || { snapshot_path: result.payload.snapshot_path, message: "Snapshot retaken" });
    button.disabled = false;
    button.textContent = original;
  }));
  document.querySelectorAll("[data-download-snapshot]").forEach((button) => {
    button.addEventListener("click", () => {
      const img = document.querySelector("[data-snapshot-preview].is-visible");
      if (!img || !img.src) {
        notify("No snapshot available yet", "warning");
        return;
      }
      const link = document.createElement("a");
      link.href = img.src;
      link.download = "attendance-snapshot.jpg";
      link.click();
    });
  });
  document.querySelectorAll("[data-capture-user]").forEach((button) => button.addEventListener("click", () => captureUserSamples(button)));
  document.querySelectorAll("[data-train]").forEach((button) => button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "Training...";
    const result = await postJSON("/train");
    button.disabled = false;
    button.textContent = result.ok ? "Model trained" : "Train model";
  }));
  document.querySelectorAll("[data-test-smtp]").forEach((button) => button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "Testing...";
    await postJSON("/settings/test-smtp");
    button.disabled = false;
    button.textContent = "Test SMTP";
  }));
  const search = document.querySelector("[data-user-search]");
  const target = document.querySelector("[data-user-results]");
  if (search && target) {
    let userSearchTimer = null;
    search.addEventListener("input", () => {
      clearTimeout(userSearchTimer);
      userSearchTimer = setTimeout(async () => {
        const response = await fetch(`/api/users/search?q=${encodeURIComponent(search.value)}`);
        const users = await response.json();
        target.innerHTML = users.length ? users.map((user) => `<div class="record-chip">${escapeHTML(user.name || user.username)} <span class="muted">${escapeHTML(user.id_number)} - ${escapeHTML(user.role)}</span></div>`).join("") : `<p class="muted mb-0">No matching users found.</p>`;
      }, 220);
    });
  }
  document.querySelectorAll("[data-table-filter]").forEach((input) => {
    let tableTimer = null;
    input.addEventListener("input", () => {
      clearTimeout(tableTimer);
      tableTimer = setTimeout(() => {
        const table = document.querySelector(input.dataset.target);
        if (table) applyTableFilters(table);
      }, 120);
    });
  });
  document.querySelectorAll("[data-clear-search]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = button.parentElement.querySelector("[data-table-filter]");
      const table = input ? document.querySelector(input.dataset.target) : null;
      if (input) input.value = "";
      if (table) applyTableFilters(table);
    });
  });
  document.querySelectorAll("[data-clear-user-search]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = document.querySelector("[data-user-search]");
      const results = document.querySelector("[data-user-results]");
      if (input) input.value = "";
      if (results) results.innerHTML = "";
    });
  });
  document.querySelectorAll("[data-filter-for]").forEach((strip) => {
    const table = document.querySelector(strip.dataset.filterFor);
    strip.querySelectorAll("[data-filter-value]").forEach((button) => {
      button.addEventListener("click", () => {
        strip.querySelectorAll("[data-filter-value]").forEach((node) => node.classList.remove("active"));
        button.classList.add("active");
        if (table) applyTableFilters(table);
      });
    });
    strip.querySelectorAll("[data-date-filter]").forEach((input) => {
      input.addEventListener("change", () => table && applyTableFilters(table));
    });
  });

  let activeLogoutTrigger = null;
  const logoutModals = Array.from(document.querySelectorAll("[data-logout-modal]"));
  logoutModals.forEach((modal) => {
    if (modal.parentElement !== document.body) document.body.appendChild(modal);
  });
  const lockLogoutModalScroll = (locked) => {
    document.body.classList.toggle("logout-modal-open", locked);
    document.documentElement.classList.toggle("logout-modal-open", locked);
  };
  const closeLogoutModal = () => {
    logoutModals.forEach((modal) => {
      modal.classList.remove("is-visible");
      modal.setAttribute("aria-hidden", "true");
    });
    lockLogoutModalScroll(false);
    if (activeLogoutTrigger) activeLogoutTrigger.focus({ preventScroll: true });
    activeLogoutTrigger = null;
  };
  const openLogoutModal = (trigger) => {
    activeLogoutTrigger = trigger;
    document.querySelectorAll(".dropdown-panel.is-open").forEach((node) => node.classList.remove("is-open"));
    logoutModals.forEach((modal) => {
      modal.classList.add("is-visible");
      modal.setAttribute("aria-hidden", "false");
      const cancelButton = modal.querySelector("[data-logout-cancel]");
      if (cancelButton) setTimeout(() => cancelButton.focus({ preventScroll: true }), 80);
    });
    lockLogoutModalScroll(true);
  };
  document.querySelectorAll("[data-logout-trigger]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      openLogoutModal(link);
    });
  });
  document.querySelectorAll("[data-logout-cancel]").forEach((button) => button.addEventListener("click", closeLogoutModal));
  logoutModals.forEach((modal) => modal.addEventListener("click", (event) => {
    if (event.target === modal) closeLogoutModal();
  }));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setMobileNav(false);
      closeLogoutModal();
      document.querySelectorAll(".dropdown-panel.is-open, .edit-modal.is-visible").forEach((node) => {
        node.classList.remove("is-visible", "is-open");
      });
    }
  });
  document.querySelectorAll("[data-notification-toggle]").forEach((button) => {
    button.addEventListener("click", async () => {
      const panel = document.querySelector("[data-notification-panel]");
      const open = !panel?.classList.contains("is-open");
      panel?.classList.toggle("is-open", open);
      button.setAttribute("aria-expanded", open ? "true" : "false");
      document.querySelector("[data-profile-panel]")?.classList.remove("is-open");
      try {
        const response = await fetch("/api/notifications");
        if (response.ok) {
          const payload = await response.json();
          setNotificationBadge(0);
          const list = document.querySelector("[data-notification-list]");
          if (list) {
            list.innerHTML = payload.items.length ? payload.items.map((item) => `<div class="activity-item"><span>${escapeHTML(item.action)}</span><small>${escapeHTML(item.time)}</small></div>`).join("") : `<p class="muted mb-0">No notifications yet.</p>`;
          }
        }
      } catch (_error) {}
    });
  });
  document.querySelectorAll("[data-notification-clear]").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      await requestJSON("/api/notifications/clear");
      setNotificationBadge(0);
      document.querySelectorAll("[data-notification-list]").forEach((list) => {
        list.innerHTML = `<p class="muted mb-0">No notifications yet.</p>`;
      });
    });
  });
  document.querySelectorAll("[data-profile-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const panel = document.querySelector("[data-profile-panel]");
      const open = !panel?.classList.contains("is-open");
      panel?.classList.toggle("is-open", open);
      button.setAttribute("aria-expanded", open ? "true" : "false");
      document.querySelector("[data-notification-panel]")?.classList.remove("is-open");
    });
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-global-search-shell]")) {
      document.querySelector("[data-global-search-results]")?.classList.remove("is-open");
    }
    if (!event.target.closest(".topbar-menu")) {
      document.querySelectorAll(".dropdown-panel").forEach((panel) => panel.classList.remove("is-open"));
      document.querySelectorAll("[data-notification-toggle], [data-profile-toggle]").forEach((button) => button.setAttribute("aria-expanded", "false"));
    }
  });
  document.querySelectorAll("[data-edit-user]").forEach((button) => {
    button.addEventListener("click", () => {
      const modal = document.querySelector(`[data-edit-modal="${button.dataset.editUser}"]`);
      if (modal) modal.classList.add("is-visible");
    });
  });
  document.querySelectorAll("[data-edit-close]").forEach((button) => {
    button.addEventListener("click", () => {
      const modal = button.closest("[data-edit-modal]");
      if (modal) modal.classList.remove("is-visible");
    });
  });
  document.querySelectorAll("[data-loading-form]").forEach((form) => {
    form.addEventListener("submit", () => {
      const button = form.querySelector("button:not([data-toggle-password])");
      if (button) {
        button.disabled = true;
        button.textContent = button.dataset.loadingText || "Working...";
      }
    });
  });
  window.addEventListener("pagehide", () => {
    const wasStreaming = Boolean(cameraController.timer);
    stopBrowserCamera();
    if (wasStreaming) {
      fetch("/camera/stop", { method: "POST", headers: { "X-Requested-With": "fetch" }, keepalive: true }).catch(() => {});
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) scheduleDashboardRefresh();
  });
  connectLiveSocket();
  if (document.querySelector("[data-metric-users]")) setInterval(() => !document.hidden && scheduleDashboardRefresh(), 10000);
});
