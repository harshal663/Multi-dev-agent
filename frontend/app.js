// ============================================================================
// MIO frontend — vanilla JS, no build step.
//
// Backend base URL resolution (fixes "frontend can't reach backend"):
//   1. ?api=http://host:port query param, if present (saved to localStorage)
//   2. a previously saved backend URL in localStorage
//   3. if this page was opened as a local file (file://), default to
//      http://localhost:8000 — same-origin relative paths can't work there
//   4. otherwise, same origin as this page (the normal case: FastAPI is
//      serving these files itself, so relative paths already work)
//
// SSE is POST-based (the backend streams via sse-starlette on a POST route),
// so we read the fetch() response body as a stream and parse SSE frames
// ourselves rather than using EventSource (which is GET-only).
// ============================================================================

function resolveApiBase() {
  const params = new URLSearchParams(window.location.search);
  const fromQuery = params.get("api");
  if (fromQuery) {
    const cleaned = fromQuery.replace(/\/$/, "");
    localStorage.setItem("mio_api_base", cleaned);
    return cleaned;
  }
  const stored = localStorage.getItem("mio_api_base");
  if (stored) return stored;
  if (window.location.protocol === "file:") return "http://localhost:8000";
  return window.location.origin;
}

let API_BASE = resolveApiBase();
let API = `${API_BASE}/api/sessions`;

const state = {
  sessionId: null,
  sessions: [],
  session: null,
  sidebarCollapsed: false,
  artifactOpen: false,
  activeFilePath: null,
  streaming: false,
  searchQuery: "",
  connected: false,
};

const el = {
  connBanner: document.getElementById("connBanner"),
  connBannerUrl: document.getElementById("connBannerUrl"),
  connBannerForm: document.getElementById("connBannerForm"),
  connBannerInput: document.getElementById("connBannerInput"),

  app: document.getElementById("app"),
  chatList: document.getElementById("chatList"),
  searchInput: document.getElementById("searchInput"),
  newChatBtn: document.getElementById("newChatBtn"),
  toggleSidebarBtn: document.getElementById("toggleSidebarBtn"),
  statusDot: document.getElementById("statusDot"),
  statusText: document.getElementById("statusText"),

  sessionTitle: document.getElementById("sessionTitle"),
  runStatus: document.getElementById("runStatus"),
  copyLinkBtn: document.getElementById("copyLinkBtn"),
  exportChatBtn: document.getElementById("exportChatBtn"),
  thread: document.getElementById("thread"),
  composerForm: document.getElementById("composerForm"),
  composerInput: document.getElementById("composerInput"),
  sendBtn: document.getElementById("sendBtn"),
  suggestionGrid: document.getElementById("suggestionGrid"),

  artifactPanel: document.getElementById("artifactPanel"),
  artifactTabs: document.getElementById("artifactTabs"),
  artifactPath: document.getElementById("artifactPath"),
  artifactVersion: document.getElementById("artifactVersion"),
  artifactEditor: document.getElementById("artifactEditor"),
  closeArtifactBtn: document.getElementById("closeArtifactBtn"),
  editNote: document.getElementById("editNote"),
  suggestEditBtn: document.getElementById("suggestEditBtn"),
  revertEditBtn: document.getElementById("revertEditBtn"),
};

// ---------------------------------------------------------------------------
// Connection handling
// ---------------------------------------------------------------------------
function setConnected(ok) {
  state.connected = ok;
  el.statusDot.className = "status-dot " + (ok ? "ok" : "bad");
  el.statusText.textContent = ok ? "Backend connected" : "Backend unreachable";
  el.connBanner.hidden = ok;
  if (!ok) {
    el.connBannerUrl.textContent = API_BASE;
    el.connBannerInput.value = API_BASE;
  }
}

async function checkConnection() {
  try {
    const res = await fetch(`${API_BASE}/api/health`, { method: "GET" });
    setConnected(res.ok);
    return res.ok;
  } catch {
    setConnected(false);
    return false;
  }
}

el.connBannerForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const value = el.connBannerInput.value.trim().replace(/\/$/, "");
  if (!value) return;
  localStorage.setItem("mio_api_base", value);
  window.location.href =
    window.location.pathname + "?api=" + encodeURIComponent(value);
});

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------
async function apiJson(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json();
}

/**
 * Streams a POST SSE endpoint, calling onEvent({event, data}) for each frame.
 * Resolves once the stream ends.
 */
async function streamSSE(path, body, onEvent) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sepIndex;
    while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
      const rawFrame = buffer.slice(0, sepIndex);
      buffer = buffer.slice(sepIndex + 2);
      let eventType = "message";
      let dataLines = [];
      for (const line of rawFrame.split("\n")) {
        if (line.startsWith("event:")) eventType = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length === 0) continue;
      let data;
      try {
        data = JSON.parse(dataLines.join("\n"));
      } catch {
        data = dataLines.join("\n");
      }
      onEvent({ event: eventType, data });
    }
  }
}

// ---------------------------------------------------------------------------
// Sidebar / sessions
// ---------------------------------------------------------------------------
async function refreshSidebar() {
  state.sessions = await apiJson(API);
  renderSidebar();
}

function groupSessionsByAge(sessions) {
  const now = Date.now() / 1000;
  const oneDay = 86400;
  const groups = { Today: [], Yesterday: [], "Previous 7 days": [], Older: [] };
  for (const s of sessions) {
    const age = now - s.updated_at;
    if (age < oneDay) groups.Today.push(s);
    else if (age < 2 * oneDay) groups.Yesterday.push(s);
    else if (age < 7 * oneDay) groups["Previous 7 days"].push(s);
    else groups.Older.push(s);
  }
  return groups;
}

function renderSidebar() {
  el.chatList.innerHTML = "";
  const q = state.searchQuery.trim().toLowerCase();
  const filtered = q
    ? state.sessions.filter((s) => (s.title || "").toLowerCase().includes(q))
    : state.sessions;

  if (filtered.length === 0) {
    const empty = document.createElement("div");
    empty.className = "chat-list-empty";
    empty.textContent = state.sessions.length === 0 ? "No chats yet" : "No matches";
    el.chatList.appendChild(empty);
    return;
  }

  const groups = groupSessionsByAge(filtered);
  for (const [label, items] of Object.entries(groups)) {
    if (items.length === 0) continue;
    const groupLabel = document.createElement("div");
    groupLabel.className = "chat-group-label";
    groupLabel.textContent = label;
    el.chatList.appendChild(groupLabel);

    for (const s of items) {
      el.chatList.appendChild(renderChatItem(s));
    }
  }
}

function renderChatItem(s) {
  const item = document.createElement("div");
  item.className = "chat-item" + (s.id === state.sessionId ? " active" : "");
  item.tabIndex = 0;
  item.innerHTML = `
    <span class="chat-item-title"></span>
    <button class="delete-chat" title="Delete chat">✕</button>
  `;
  item.querySelector(".chat-item-title").textContent = s.title || "New chat";
  item.addEventListener("click", (e) => {
    if (e.target.closest(".delete-chat")) return;
    openSession(s.id);
  });
  item.querySelector(".delete-chat").addEventListener("click", async (e) => {
    e.stopPropagation();
    await apiJson(`${API}/${s.id}`, { method: "DELETE" });
    if (state.sessionId === s.id) {
      state.sessionId = null;
      state.session = null;
      renderThread();
    }
    await refreshSidebar();
  });
  return item;
}

el.searchInput.addEventListener("input", (e) => {
  state.searchQuery = e.target.value;
  renderSidebar();
});

async function createSession() {
  const session = await apiJson(API, { method: "POST" });
  await refreshSidebar();
  await openSession(session.id);
}

async function openSession(id) {
  state.sessionId = id;
  state.session = await apiJson(`${API}/${id}`);
  closeArtifact();
  renderSidebar();
  renderThread();
}

// ---------------------------------------------------------------------------
// Thread rendering
// ---------------------------------------------------------------------------
const ROLE_LABELS = {
  architect: "Architect",
  coder: "Coder",
  reviewer: "Code Reviewer",
  qa: "QA Tester",
  system: "MIO",
};

function renderThread() {
  el.thread.innerHTML = "";
  el.sessionTitle.textContent = state.session ? (state.session.title || "New chat") : "";

  if (!state.session || state.session.messages.length === 0) {
    el.thread.appendChild(makeEmptyState());
    return;
  }

  for (const msg of state.session.messages) {
    el.thread.appendChild(renderMessage(msg));
  }
  el.thread.scrollTop = el.thread.scrollHeight;
}

function makeEmptyState() {
  const div = document.createElement("div");
  div.className = "empty-state";
  div.innerHTML = `
    <div class="orb"></div>
    <h1>What are we building today?</h1>
    <p>Describe a feature or a bug fix. The Architect scopes it, the Coder writes it,
       and the Reviewer + QA Tester check it in a sandboxed Docker environment —
       streamed live below.</p>
  `;
  return div;
}

function renderMessage(msg) {
  const row = document.createElement("div");
  row.className = `msg-row ${msg.role === "user" ? "user" : "agent"}`;

  if (msg.role === "user") {
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = msg.content;
    row.appendChild(bubble);
    return row;
  }

  const label = document.createElement("div");
  label.className = `agent-label role-${msg.role}`;
  label.innerHTML = `<span class="dot dot-${msg.role}"></span>${ROLE_LABELS[msg.role] || msg.role}`;
  row.appendChild(label);

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = msg.content;
  row.appendChild(bubble);

  if (msg.files && msg.files.length) {
    const chips = document.createElement("div");
    chips.className = "file-chips";
    for (const f of msg.files) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "file-chip";
      chip.innerHTML = `<span>${f.path}</span><span class="file-chip-badge">v${f.version}</span>`;
      chip.addEventListener("click", () => openArtifact(f.path));
      chips.appendChild(chip);
    }
    bubble.appendChild(chips);
  }

  return row;
}

function appendNodeTrace(text) {
  const trace = document.createElement("div");
  trace.className = "node-trace";
  trace.textContent = text;
  el.thread.appendChild(trace);
  el.thread.scrollTop = el.thread.scrollHeight;
  return trace;
}

// ---------------------------------------------------------------------------
// Sending messages / streaming the agent run
// ---------------------------------------------------------------------------
async function sendMessage(content) {
  if (!content.trim() || state.streaming) return;
  if (!(await checkConnection())) {
    appendNodeTrace("⚠ Backend unreachable — set the correct backend URL above.");
    return;
  }
  if (!state.sessionId) await createSession();

  state.session.messages.push({ id: "tmp", role: "user", content, files: [], meta: {} });
  renderThread();

  setStreaming(true);
  try {
    await streamSSE(`${API}/${state.sessionId}/messages`, { content }, handleStreamEvent);
  } catch (err) {
    appendNodeTrace(`⚠ ${err.message}`);
  } finally {
    setStreaming(false);
    try {
      state.session = await apiJson(`${API}/${state.sessionId}`);
      renderThread();
      await refreshSidebar();
    } catch (err) {
      appendNodeTrace(`⚠ Couldn't refresh session: ${err.message}`);
    }
  }
}

function handleStreamEvent({ event, data }) {
  if (event === "node_update") {
    const NODE_VERB = {
      architect: "Architect is scoping the plan…",
      coder: "Coder is writing files…",
      reviewer: "Reviewer is checking the code…",
      qa: "QA Tester is verifying…",
    };
    el.runStatus.textContent = NODE_VERB[data.node] || `${data.node}…`;
    appendNodeTrace(`· ${data.message || data.node}`);
  } else if (event === "error") {
    appendNodeTrace(`⚠ Agent error: ${data.error}`);
  }
}

function setStreaming(on) {
  state.streaming = on;
  el.sendBtn.disabled = on;
  el.runStatus.classList.toggle("active", on);
  if (!on) el.runStatus.textContent = "";
}

// ---------------------------------------------------------------------------
// Artifact panel
// ---------------------------------------------------------------------------
function openArtifact(path) {
  state.activeFilePath = path;
  state.artifactOpen = true;
  el.app.classList.add("artifact-open");
  renderArtifact();
}

function closeArtifact() {
  state.artifactOpen = false;
  state.activeFilePath = null;
  el.app.classList.remove("artifact-open");
}

function renderArtifact() {
  if (!state.session || !state.activeFilePath) return;
  const files = Object.values(state.session.files || {});
  const file = state.session.files[state.activeFilePath];
  if (!file) return;

  el.artifactTabs.innerHTML = "";
  for (const f of files) {
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = "artifact-tab" + (f.path === state.activeFilePath ? " active" : "");
    tab.textContent = f.path;
    tab.addEventListener("click", () => openArtifact(f.path));
    el.artifactTabs.appendChild(tab);
  }

  el.artifactPath.textContent = file.path;
  el.artifactVersion.textContent = `v${file.version} · edited by ${file.last_edited_by}`;
  el.artifactEditor.value = file.content;
  el.editNote.value = "";
}

el.closeArtifactBtn.addEventListener("click", closeArtifact);

el.revertEditBtn.addEventListener("click", () => {
  const file = state.session.files[state.activeFilePath];
  if (file) el.artifactEditor.value = file.content;
});

el.suggestEditBtn.addEventListener("click", async () => {
  const path = state.activeFilePath;
  const editedContent = el.artifactEditor.value;
  const note = el.editNote.value.trim();
  if (!path || state.streaming) return;

  setStreaming(true);
  el.suggestEditBtn.disabled = true;
  try {
    await streamSSE(
      `${API}/${state.sessionId}/files/edit`,
      { path, edited_content: editedContent, note: note || undefined },
      handleStreamEvent
    );
  } catch (err) {
    appendNodeTrace(`⚠ ${err.message}`);
  } finally {
    setStreaming(false);
    el.suggestEditBtn.disabled = false;
    try {
      state.session = await apiJson(`${API}/${state.sessionId}`);
      renderThread();
      renderArtifact();
      await refreshSidebar();
    } catch (err) {
      appendNodeTrace(`⚠ Couldn't refresh session: ${err.message}`);
    }
  }
});

// ---------------------------------------------------------------------------
// Composer + suggestion cards
// ---------------------------------------------------------------------------
el.composerForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = el.composerInput.value;
  el.composerInput.value = "";
  el.composerInput.style.height = "auto";
  sendMessage(text);
});

el.composerInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    el.composerForm.requestSubmit();
  }
});

el.composerInput.addEventListener("input", () => {
  el.composerInput.style.height = "auto";
  el.composerInput.style.height = Math.min(el.composerInput.scrollHeight, 160) + "px";
});

el.suggestionGrid.addEventListener("click", (e) => {
  const card = e.target.closest(".suggestion-card");
  if (!card) return;
  el.composerInput.value = card.dataset.prompt || "";
  el.composerInput.focus();
  el.composerInput.dispatchEvent(new Event("input"));
});

// ---------------------------------------------------------------------------
// Header actions
// ---------------------------------------------------------------------------
el.copyLinkBtn.addEventListener("click", async () => {
  if (!state.sessionId) return;
  const url = `${window.location.origin}${window.location.pathname}#${state.sessionId}`;
  try {
    await navigator.clipboard.writeText(url);
    el.copyLinkBtn.title = "Copied!";
    setTimeout(() => (el.copyLinkBtn.title = "Copy chat link"), 1500);
  } catch {
    appendNodeTrace("⚠ Couldn't copy to clipboard in this browser.");
  }
});

el.exportChatBtn.addEventListener("click", () => {
  if (!state.session) return;
  const blob = new Blob([JSON.stringify(state.session, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${(state.session.title || "chat").replace(/[^\w-]+/g, "_")}.json`;
  a.click();
  URL.revokeObjectURL(url);
});

// ---------------------------------------------------------------------------
// New chat / sidebar toggle
// ---------------------------------------------------------------------------
el.newChatBtn.addEventListener("click", createSession);

el.toggleSidebarBtn.addEventListener("click", () => {
  state.sidebarCollapsed = !state.sidebarCollapsed;
  el.app.classList.toggle("sidebar-collapsed", state.sidebarCollapsed);
});

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
(async function init() {
  const ok = await checkConnection();
  if (!ok) {
    renderThread();
    return; // banner is showing; user can point it at the right backend
  }
  try {
    await refreshSidebar();
    if (state.sessions.length > 0) {
      await openSession(state.sessions[0].id);
    } else {
      renderThread();
    }
  } catch (err) {
    setConnected(false);
    renderThread();
  }
  // Keep the status dot honest if the backend goes away mid-session.
  setInterval(checkConnection, 15000);
})();
