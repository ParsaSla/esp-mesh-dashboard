const CHAT_STORE = "espMeshChatHistory";

const els = {
  modelTag: document.getElementById("ai-model-tag"),
  scope: document.getElementById("scope-select"),
  clearBtn: document.getElementById("chat-clear-btn"),
  messages: document.getElementById("chat-messages"),
  form: document.getElementById("chat-form"),
  input: document.getElementById("chat-input"),
  sendBtn: document.getElementById("chat-send-btn"),
};

const state = {
  history: [],
  loading: false,
  scope: "all",
};
const nodeNames = new Map();

function persistHistory() {
  try {
    localStorage.setItem(CHAT_STORE, JSON.stringify(state.history.slice(-40)));
  } catch {
    // The chat still works when browser storage is unavailable.
  }
}

function restoreHistory() {
  try {
    const saved = JSON.parse(localStorage.getItem(CHAT_STORE) || "[]");
    state.history = Array.isArray(saved)
      ? saved
        .filter((item) => item && ["user", "assistant"].includes(item.role) && typeof item.content === "string")
        .slice(-40)
      : [];
  } catch {
    state.history = [];
  }
}

function appendMessage(role, text) {
  const message = document.createElement("div");
  message.className = `chat-msg chat-msg-${role}`;

  const header = document.createElement("div");
  header.className = "chat-msg-header";
  const roleLabel = document.createElement("span");
  roleLabel.className = "chat-role";
  roleLabel.textContent = role === "user" ? "[USER]" : "[AI]";
  header.appendChild(roleLabel);

  if (role === "assistant" && text && text !== "thinking…") {
    const copyButton = document.createElement("button");
    copyButton.type = "button";
    copyButton.className = "copy-btn";
    copyButton.textContent = "COPY";
    copyButton.title = "Copy message to clipboard";
    copyButton.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(text);
        copyButton.textContent = "COPIED";
        setTimeout(() => { copyButton.textContent = "COPY"; }, 1500);
      } catch {
        copyButton.textContent = "UNAVAILABLE";
      }
    });
    header.appendChild(copyButton);
  }

  const body = document.createElement("div");
  body.className = "chat-text";
  body.textContent = text;
  message.append(header, body);
  els.messages.appendChild(message);
  els.messages.scrollTop = els.messages.scrollHeight;
  return message;
}

function renderTranscript() {
  els.messages.replaceChildren();
  if (!state.history.length) {
    appendMessage(
      "assistant",
      "Ready. Ask about network health, hop paths, signal strength, or weather physics (e.g. dew point and rain likelihood)."
    );
    return;
  }
  state.history.forEach(({ role, content }) => appendMessage(role, content));
}

async function loadStatus() {
  try {
    const response = await fetch("/api/ai/status");
    if (!response.ok) throw new Error(`AI status request failed (${response.status})`);
    const data = await response.json();
    els.modelTag.textContent = data.configured && data.model ? data.model.toUpperCase() : "OFFLINE";
    els.modelTag.dataset.state = data.configured && data.model ? "live" : "stale";
  } catch {
    els.modelTag.textContent = "OFFLINE";
    els.modelTag.dataset.state = "stale";
  }
}

async function loadNodes() {
  const wantedNode = new URLSearchParams(location.search).get("node") || "";
  try {
    const response = await fetch("/nodes");
    if (!response.ok) throw new Error(`Station request failed (${response.status})`);
    const data = await response.json();
    const nodes = Array.isArray(data.nodes) ? data.nodes : [];
    nodes.forEach((node) => {
      const option = document.createElement("option");
      option.value = node.nodeId;
      option.textContent = node.displayName || node.nodeId;
      nodeNames.set(node.nodeId, node.displayName || node.nodeId);
      els.scope.appendChild(option);
    });
    if (wantedNode && nodes.some((node) => node.nodeId === wantedNode)) {
      els.scope.value = wantedNode;
      state.scope = wantedNode;
    }
  } catch {
    state.scope = "all";
  }
}

async function sendMessage(promptText) {
  const text = (promptText !== undefined ? promptText : els.input.value).trim();
  if (!text || state.loading) return;

  els.input.value = "";
  state.loading = true;
  els.sendBtn.disabled = true;
  els.input.disabled = true;
  appendMessage("user", text);
  state.history.push({ role: "user", content: text });
  persistHistory();
  const pending = appendMessage("assistant", "thinking…");

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        nodeId: state.scope === "all" ? null : state.scope,
        history: state.history.slice(0, -1).slice(-8),
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || `AI request failed (${response.status})`);
    }
    if (pending.isConnected) pending.remove();
    const reply = data.reply || "No response from AI.";
    appendMessage("assistant", reply);
    state.history.push({ role: "assistant", content: reply });
    persistHistory();
    if (data.model) {
      els.modelTag.textContent = data.model.toUpperCase();
      els.modelTag.dataset.state = data.error ? "stale" : "live";
    }
  } catch (error) {
    if (pending.isConnected) pending.remove();
    const detail = error instanceof Error ? error.message : "Unknown request error";
    const reply = `Unable to communicate with the AI service: ${detail}`;
    appendMessage("assistant", reply);
    state.history.push({ role: "assistant", content: reply });
    persistHistory();
  } finally {
    state.loading = false;
    els.sendBtn.disabled = false;
    els.input.disabled = false;
    els.input.focus();
  }
}

els.form.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage();
});

els.scope.addEventListener("change", () => {
  state.scope = els.scope.value;
  appendMessage(
    "assistant",
    state.scope === "all"
      ? "Context switched to ALL STATIONS (entire mesh network)."
      : `Context switched to ${nodeNames.get(state.scope) || state.scope}.`
  );
});

els.clearBtn.addEventListener("click", () => {
  state.history = [];
  try {
    localStorage.removeItem(CHAT_STORE);
  } catch {
    // The in-memory transcript is still cleared.
  }
  renderTranscript();
});

document.querySelectorAll(".quick-btn").forEach((button) => {
  button.addEventListener("click", () => {
    const prompt = button.getAttribute("data-prompt");
    if (prompt) sendMessage(prompt);
  });
});

window.addEventListener("storage", (event) => {
  if (event.key !== CHAT_STORE) return;
  restoreHistory();
  renderTranscript();
});

restoreHistory();
renderTranscript();
loadStatus();
loadNodes();
els.input.focus();
