function attendanceChart(canvasId, chartData, label = "Attendance") {
  const canvas = document.getElementById(canvasId);
  if (!canvas || !window.Chart) return;
  new Chart(canvas, {
    type: "line",
    data: {
      labels: chartData.labels,
      datasets: [{
        label,
        data: chartData.values,
        borderColor: "#58d7ff",
        backgroundColor: "rgba(88,215,255,.18)",
        fill: true,
        tension: .35
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { labels: { color: "#ecf2ff" } } },
      scales: {
        x: { ticks: { color: "#9fb0cc" }, grid: { color: "rgba(255,255,255,.06)" } },
        y: { ticks: { color: "#9fb0cc", precision: 0 }, grid: { color: "rgba(255,255,255,.06)" } }
      }
    }
  });
}
