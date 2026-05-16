const attendanceCharts = new Map();

function chartSignature(chartData) {
  return JSON.stringify([chartData?.labels || [], chartData?.values || []]);
}

function attendanceChart(canvasId, chartData, label = "Attendance") {
  const canvas = document.getElementById(canvasId);
  if (!canvas || !window.Chart) return;
  const signature = chartSignature(chartData);
  const existing = attendanceCharts.get(canvasId);
  if (existing) {
    if (existing.signature === signature) return;
    existing.chart.data.labels = chartData.labels;
    existing.chart.data.datasets[0].data = chartData.values;
    existing.signature = signature;
    existing.chart.update("active");
    return;
  }
  const chart = new Chart(canvas, {
    type: "line",
    data: {
      labels: chartData.labels,
      datasets: [{
        label,
        data: chartData.values,
        borderColor: "#58d7ff",
        backgroundColor: "rgba(88,215,255,.18)",
        fill: true,
        tension: .35,
        pointRadius: 2,
        pointHoverRadius: 4
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 420, easing: "easeOutQuart" },
      resizeDelay: 180,
      plugins: { legend: { labels: { color: "#ecf2ff" } } },
      scales: {
        x: { ticks: { color: "#9fb0cc", maxRotation: 0 }, grid: { color: "rgba(255,255,255,.06)" } },
        y: { ticks: { color: "#9fb0cc", precision: 0 }, grid: { color: "rgba(255,255,255,.06)" } }
      }
    }
  });
  attendanceCharts.set(canvasId, { chart, signature, label });
}

function attendanceDonutChart(canvasId, chartData, label = "Distribution") {
  const canvas = document.getElementById(canvasId);
  if (!canvas || !window.Chart) return;
  const signature = chartSignature(chartData);
  const existing = attendanceCharts.get(canvasId);
  if (existing) {
    if (existing.signature === signature) return;
    existing.chart.data.labels = chartData.labels;
    existing.chart.data.datasets[0].data = chartData.values;
    existing.signature = signature;
    existing.chart.update("active");
    return;
  }
  const chart = new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: chartData.labels,
      datasets: [{
        label,
        data: chartData.values,
        backgroundColor: ["#66dbff", "#7cb9ff", "#48deb1", "#ffd36f", "#f7a3db"],
        borderColor: "rgba(7,17,31,.9)",
        borderWidth: 3
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "68%",
      animation: { duration: 360, easing: "easeOutQuart" },
      resizeDelay: 180,
      plugins: { legend: { position: "bottom", labels: { color: "#ecf2ff", boxWidth: 12 } } }
    }
  });
  attendanceCharts.set(canvasId, { chart, signature, label });
}

function updateAttendanceCharts(payload = {}) {
  if (payload.chart) attendanceChart("adminChart", payload.chart, "Weekly analytics");
  if (payload.role_chart) attendanceDonutChart("roleChart", payload.role_chart, "Role distribution");
  if (payload.accuracy_chart) attendanceChart("accuracyChart", payload.accuracy_chart, "Recognition accuracy trends");
}
