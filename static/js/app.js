async function postJSON(url) {
  const response = await fetch(url, { method: "POST", headers: { "X-Requested-With": "fetch" } });
  const payload = await response.json();
  notify(payload.message || "Done", response.ok ? "success" : "danger");
  return { ok: response.ok, payload };
}
let lastSuccessKey = "";
let lastLiveLogKey = "";
function snapshotURL(path) {
  if (!path) return "";
  return path.startsWith("/") ? path : `/${path}`;
}
function updateCameraStage(state, headline, detail) {
  document.querySelectorAll("[data-camera-stage]").forEach((stage) => {
    stage.classList.remove("is-live", "is-loading", "is-error");
    if (state) stage.classList.add(`is-${state}`);
    const placeholder = stage.querySelector("[data-camera-placeholder]");
    if (!placeholder) return;
    const strong = placeholder.querySelector("strong");
    const span = placeholder.querySelector("span");
    if (strong && headline) strong.textContent = headline;
    if (span && detail) span.textContent = detail;
  });
}
function setCameraStatus(message) {
  document.querySelectorAll("[data-camera-status]").forEach((node) => {
    node.textContent = message;
  });
}
function updateIdentity(event) {
  const setText = (selector, value) => document.querySelectorAll(selector).forEach((node) => { node.textContent = value || "-"; });
  setText("[data-fps]", event.fps || 0);
  setText("[data-recognition-state]", event.message || "Scanning");
  setText("[data-live-name]", event.name || "-");
  setText("[data-live-role]", event.role || "-");
  setText("[data-live-id]", event.id_number || "-");
  setText("[data-live-date]", event.date || "-");
  setText("[data-live-time]", event.time || "-");
  setText("[data-live-accuracy]", event.recognition_accuracy ?? event.confidence ?? 0);
  document.querySelectorAll("[data-live-status]").forEach((node) => { node.textContent = event.status || "Waiting"; });
  document.querySelectorAll("[data-snapshot-preview]").forEach((img) => {
    const empty = img.parentElement.querySelector("[data-snapshot-empty]");
    if (event.snapshot_path) {
      img.src = snapshotURL(event.snapshot_path);
      img.classList.add("is-visible");
      if (empty) empty.style.display = "none";
    }
  });
  document.querySelectorAll("[data-live-logs]").forEach((list) => {
    if (!event.message) return;
    const logKey = event.state === "unknown" ? event.message : `${event.message}-${event.time || ""}`;
    if (logKey === lastLiveLogKey) return;
    lastLiveLogKey = logKey;
    const item = document.createElement("div");
    item.className = "activity-item";
    item.innerHTML = `<span>${event.message}</span><small>${event.time || new Date().toLocaleTimeString()}</small>`;
    list.prepend(item);
    while (list.children.length > 6) list.lastElementChild.remove();
  });
  const successKey = `${event.id_number || ""}-${event.date || ""}-${event.time || ""}`;
  if (event.state === "recognized" && event.message === "Attendance Marked Successfully" && successKey !== lastSuccessKey) {
    lastSuccessKey = successKey;
    const pop = document.querySelector("[data-success-pop]");
    if (pop) {
      const detail = pop.querySelector("[data-success-detail]");
      if (detail) detail.textContent = `${event.name} - ${event.role} - ${event.time} - ${event.status}`;
      pop.classList.add("is-visible");
      setTimeout(() => pop.classList.remove("is-visible"), 3600);
    }
    notify(`${event.name} marked present`, "success");
    playSuccessTone();
  }
}
function incrementNotification(message) {
  document.querySelectorAll("[data-notification-badge]").forEach((badge) => {
    const raw = badge.textContent === "99+" ? 99 : Number(badge.textContent || 0);
    const next = Math.min(raw + 1, 100);
    badge.textContent = next > 99 ? "99+" : String(next);
    badge.classList.toggle("is-empty", next === 0);
  });
  document.querySelectorAll("[data-notification-list]").forEach((list) => {
    const empty = list.querySelector("p");
    if (empty) empty.remove();
    const item = document.createElement("div");
    item.className = "activity-item";
    item.innerHTML = `<span>${message}</span><small>${new Date().toLocaleTimeString()}</small>`;
    list.prepend(item);
    while (list.children.length > 20) list.lastElementChild.remove();
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
async function refreshDashboard() {
  const users = document.querySelector("[data-metric-users]");
  if (!users) return;
  try {
    const response = await fetch("/api/dashboard/summary");
    if (!response.ok) return;
    const payload = await response.json();
    document.querySelector("[data-metric-users]").textContent = payload.metrics.users;
    document.querySelector("[data-metric-today]").textContent = payload.metrics.today_present;
    document.querySelector("[data-metric-camera]").textContent = payload.metrics.camera;
    const tbody = document.querySelector("[data-recent-rows]");
    if (tbody && payload.recent.length) {
      tbody.innerHTML = payload.recent.map((row) => `<tr data-role="${row.role}" data-status="${String(row.status).toLowerCase()}" data-date="${row.date}"><td>${row.id_number}</td><td>${row.name}</td><td>${row.role}</td><td>${row.date}</td><td>${row.time}</td><td><span class="badge-soft">${row.status}</span></td><td>${row.accuracy || 0}%</td><td>${row.snapshot_path ? `<a class="snapshot-link" href="/${row.snapshot_path}" target="_blank">Preview</a>` : `<span class="muted">Pending</span>`}</td></tr>`).join("");
      applyTableFilters(tbody.closest("table"));
    }
  } catch (_error) {}
}
function attachFeedHandlers(feed) {
  feed.addEventListener("load", () => {
    updateCameraStage("live", "Live recognition feed", "Camera stream is active.");
    setCameraStatus("Camera active");
  });
  feed.addEventListener("error", async () => {
    updateCameraStage("error", "Feed unavailable", "The camera stream did not return a usable frame.");
    try {
      const response = await fetch("/api/camera/status");
      if (response.ok) {
        const payload = await response.json();
        setCameraStatus(payload.message || "Camera unavailable");
      }
    } catch (_error) {
      setCameraStatus("Camera unavailable");
    }
  });
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
document.addEventListener("DOMContentLoaded", () => {
  document.body.classList.add("page-ready");
  document.querySelectorAll("[data-notification-badge]").forEach((badge) => {
    badge.classList.toggle("is-empty", badge.textContent === "0");
  });
  document.querySelectorAll("[data-count-to]").forEach((node) => {
    const target = Number(node.dataset.countTo || node.textContent || 0);
    const start = performance.now();
    const step = (time) => {
      const progress = Math.min((time - start) / 900, 1);
      node.textContent = Math.round(target * progress);
      if (progress < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
  document.querySelectorAll("[data-toggle-password]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = button.parentElement.querySelector("input");
      if (!input) return;
      input.type = input.type === "password" ? "text" : "password";
      button.textContent = input.type === "password" ? "Show" : "Hide";
    });
  });
  document.querySelectorAll("[data-video-feed]").forEach(attachFeedHandlers);
  document.querySelectorAll("[data-camera-start]").forEach((button) => button.addEventListener("click", async () => {
    updateCameraStage("loading", "Opening camera", "Waiting for the first video frame.");
    setCameraStatus("Starting camera...");
    const result = await postJSON("/camera/start");
    if (result.ok) {
      document.querySelectorAll("[data-video-feed]").forEach((feed) => {
        feed.src = `/video-feed?ts=${Date.now()}`;
      });
    } else {
      updateCameraStage("error", "Camera unavailable", result.payload.message || "Unable to start the webcam.");
      setCameraStatus(result.payload.message || "Camera unavailable");
    }
  }));
  document.querySelectorAll("[data-snapshot]").forEach((button) => button.addEventListener("click", async () => {
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "Capturing...";
    const result = await postJSON("/camera/snapshot");
    if (result.ok && result.payload.snapshot_path) {
      updateIdentity({ snapshot_path: result.payload.snapshot_path, message: "Snapshot captured" });
    }
    button.disabled = false;
    button.textContent = original;
  }));
  document.querySelectorAll("[data-retake-snapshot]").forEach((button) => button.addEventListener("click", async () => {
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "Retaking...";
    const result = await postJSON("/camera/snapshot?retake=1");
    if (result.ok && result.payload.snapshot_path) {
      updateIdentity({ snapshot_path: result.payload.snapshot_path, message: "Snapshot retaken" });
    }
    button.disabled = false;
    button.textContent = original;
  }));
  document.querySelectorAll("[data-camera-stop]").forEach((button) => button.addEventListener("click", async () => {
    await postJSON("/camera/stop");
    document.querySelectorAll("[data-video-feed]").forEach((feed) => {
      feed.removeAttribute("src");
    });
    updateCameraStage("", "Live recognition feed", "Press Camera on to start the webcam stream.");
    setCameraStatus("Camera idle");
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
    search.addEventListener("input", async () => {
      const response = await fetch(`/api/users/search?q=${encodeURIComponent(search.value)}`);
      const users = await response.json();
      target.innerHTML = users.length ? users.map((user) => `<div class="record-chip">${user.name || user.username} <span class="muted">${user.id_number} - ${user.role}</span></div>`).join("") : `<p class="muted mb-0">No matching users found.</p>`;
    });
  }
  document.querySelectorAll("[data-table-filter]").forEach((input) => {
    input.addEventListener("input", () => {
      const table = document.querySelector(input.dataset.target);
      if (!table) return;
      applyTableFilters(table);
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
      const target = document.querySelector("[data-user-results]");
      if (input) input.value = "";
      if (target) target.innerHTML = "";
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
  document.querySelectorAll("[data-logout-cancel]").forEach((button) => {
    button.addEventListener("click", closeLogoutModal);
  });
  logoutModals.forEach((modal) => {
    modal.addEventListener("click", (event) => {
      if (event.target === modal) closeLogoutModal();
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeLogoutModal();
      document.querySelectorAll(".dropdown-panel.is-open, .edit-modal.is-visible").forEach((node) => {
        node.classList.remove("is-visible", "is-open");
      });
    }
  });
  document.querySelectorAll("[data-notification-toggle]").forEach((button) => {
    button.addEventListener("click", async () => {
      const panel = document.querySelector("[data-notification-panel]");
      panel?.classList.toggle("is-open");
      document.querySelector("[data-profile-panel]")?.classList.remove("is-open");
      try {
        const response = await fetch("/api/notifications");
        if (response.ok) {
          const payload = await response.json();
          document.querySelectorAll("[data-notification-badge]").forEach((badge) => {
            badge.textContent = "0";
            badge.classList.add("is-empty");
          });
          const list = document.querySelector("[data-notification-list]");
          if (list) {
            list.innerHTML = payload.items.length ? payload.items.map((item) => `<div class="activity-item"><span>${item.action}</span><small>${item.time}</small></div>`).join("") : `<p class="muted mb-0">No notifications yet.</p>`;
          }
        }
      } catch (_error) {}
    });
  });
  document.querySelectorAll("[data-profile-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelector("[data-profile-panel]")?.classList.toggle("is-open");
      document.querySelector("[data-notification-panel]")?.classList.remove("is-open");
    });
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".topbar-menu")) {
      document.querySelectorAll(".dropdown-panel").forEach((panel) => panel.classList.remove("is-open"));
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
      const button = form.querySelector("button");
      if (button) {
        button.disabled = true;
        button.textContent = button.dataset.loadingText || "Working...";
      }
    });
  });
  if (document.querySelector("[data-live-name]") || document.querySelector("[data-metric-users]")) {
    setInterval(async () => {
      try {
        const response = await fetch("/api/camera/status");
        if (response.ok) {
          const payload = await response.json();
          setCameraStatus(payload.message || payload.camera);
          updateIdentity(payload);
          refreshDashboard();
        }
      } catch (_error) {}
    }, 2200);
  }
});
