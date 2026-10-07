const statusBadge = document.getElementById("gateway-status");
const nodeIdEl = document.getElementById("node-id");
const temperatureEl = document.getElementById("temperature");
const humidityEl = document.getElementById("humidity");
const sequenceEl = document.getElementById("sequence");
const updatedAtEl = document.getElementById("updated-at");
const messageLogEl = document.getElementById("message-log");
const chartCanvas = document.getElementById("telemetry-chart");
const ctx = chartCanvas.getContext("2d");

const state = {
  nodeId: null,
  samples: [],
  lastEvent: null,
};

function setStatus(connected) {
  statusBadge.classList.toggle("connected", connected);
  statusBadge.classList.toggle("disconnected", !connected);
  statusBadge.textContent = connected ? "Gateway connected" : "Gateway disconnected";
}

function renderMessageLog() {
  const messages = Array.from(messageLogEl.children);
  if (messages.length > 8) {
    messages.slice(0, messages.length - 8).forEach((item) => item.remove());
  }
}

function pushLog(message) {
  const item = document.createElement("li");
  item.textContent = message;
  messageLogEl.prepend(item);
  renderMessageLog();
}

function renderCurrentNode() {
  if (!state.lastEvent || !state.lastEvent.payload) {
    nodeIdEl.textContent = "--";
    temperatureEl.textContent = "--";
    humidityEl.textContent = "--";
    sequenceEl.textContent = "--";
    updatedAtEl.textContent = "Waiting for data";
    return;
  }

  const { nodeId, timestamp, sequence, payload } = state.lastEvent;
  nodeIdEl.textContent = nodeId || "--";
  temperatureEl.textContent = payload.temperature != null ? `${payload.temperature.toFixed(1)} °C` : "--";
  humidityEl.textContent = payload.humidity != null ? `${payload.humidity.toFixed(0)} %` : "--";
  sequenceEl.textContent = sequence ?? "--";
  updatedAtEl.textContent = new Date(timestamp).toLocaleString();
}

function drawChart() {
  const width = chartCanvas.width;
  const height = chartCanvas.height;
  ctx.clearRect(0, 0, width, height);

  const padding = 30;
  const chartLeft = padding;
  const chartRight = width - padding;
  const chartTop = padding;
  const chartBottom = height - padding;

  ctx.strokeStyle = "rgba(158, 181, 202, 0.6)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(chartLeft, chartTop);
  ctx.lineTo(chartLeft, chartBottom);
  ctx.lineTo(chartRight, chartBottom);
  ctx.stroke();

  if (!state.samples.length) {
    ctx.fillStyle = "rgba(158, 181, 202, 0.8)";
    ctx.font = "16px Segoe UI";
    ctx.fillText("Waiting for telemetry", chartLeft + 10, chartTop + 25);
    return;
  }

  const maxTemp = Math.max(...state.samples.map((sample) => sample.temperature ?? 0), 40);
  const maxHumidity = Math.max(...state.samples.map((sample) => sample.humidity ?? 0), 100);

  const tempPoints = state.samples.map((sample, index) => {
    const x = chartLeft + (index / Math.max(1, state.samples.length - 1)) * (chartRight - chartLeft);
    const y = chartBottom - ((sample.temperature ?? 0) / maxTemp) * (chartBottom - chartTop);
    return { x, y };
  });

  const humidityPoints = state.samples.map((sample, index) => {
    const x = chartLeft + (index / Math.max(1, state.samples.length - 1)) * (chartRight - chartLeft);
    const y = chartBottom - ((sample.humidity ?? 0) / maxHumidity) * (chartBottom - chartTop);
    return { x, y };
  });

  ctx.strokeStyle = "#42d1b8";
  ctx.beginPath();
  tempPoints.forEach((point, idx) => {
    if (idx === 0) {
      ctx.moveTo(point.x, point.y);
    } else {
      ctx.lineTo(point.x, point.y);
    }
  });
  ctx.stroke();

  ctx.strokeStyle = "#6ea8ff";
  ctx.beginPath();
  humidityPoints.forEach((point, idx) => {
    if (idx === 0) {
      ctx.moveTo(point.x, point.y);
    } else {
      ctx.lineTo(point.x, point.y);
    }
  });
  ctx.stroke();

  ctx.fillStyle = "rgba(158, 181, 202, 0.8)";
  ctx.font = "12px Segoe UI";
  ctx.fillText("Temp", chartRight - 38, chartTop + 10);
  ctx.fillText("Humidity", chartRight - 58, chartTop + 26);
}

async function loadHistory(nodeId) {
  const response = await fetch(`/nodes/${encodeURIComponent(nodeId)}/history?limit=50`);
  const data = await response.json();
  state.samples = (data.samples || []).map((sample) => ({
    temperature: sample.temperature ?? null,
    humidity: sample.humidity ?? null,
    timestamp: sample.timestamp,
    sequence: sample.sequence,
  }));
  drawChart();
}

async function loadNodes() {
  const response = await fetch("/nodes");
  const data = await response.json();
  const nodes = data.nodes || [];

  if (!nodes.length) {
    state.lastEvent = null;
    renderCurrentNode();
    drawChart();
    return;
  }

  const node = nodes[0];
  state.nodeId = node.nodeId;
  state.lastEvent = node;
  renderCurrentNode();
  await loadHistory(node.nodeId);
}

async function refreshHealth() {
  const response = await fetch("/health");
  const data = await response.json();
  setStatus(Boolean(data.gatewayConnected));
  if (!data.gatewayConnected) {
    pushLog(data.gatewayMessage || "Gateway offline");
  }
}

function setupSocket() {
  const socketUrl = `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/live`;
  const socket = new WebSocket(socketUrl);

  socket.addEventListener("open", () => {
    setStatus(true);
    pushLog("Connected to live telemetry stream");
  });

  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "status") {
      setStatus(Boolean(message.gatewayConnected));
      return;
    }

    if (message.type !== "telemetry") {
      return;
    }

    state.lastEvent = message;
    state.samples.push({
      temperature: message.payload.temperature ?? null,
      humidity: message.payload.humidity ?? null,
      timestamp: message.timestamp,
      sequence: message.sequence,
    });

    if (state.samples.length > 60) {
      state.samples = state.samples.slice(-60);
    }

    renderCurrentNode();
    drawChart();
    pushLog(`${message.nodeId} @ ${new Date(message.timestamp).toLocaleTimeString()} | ${message.payload.temperature ?? "--"}°C / ${message.payload.humidity ?? "--"}%`);
  });

  socket.addEventListener("close", () => {
    setStatus(false);
    pushLog("WebSocket disconnected; retrying...");
    setTimeout(setupSocket, 2000);
  });
}

async function initialize() {
  await refreshHealth();
  await loadNodes();
  setupSocket();
}

initialize();
