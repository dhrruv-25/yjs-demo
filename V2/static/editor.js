import * as Y from "https://esm.sh/yjs@13.6.33";
import {
  Awareness,
  applyAwarenessUpdate,
  encodeAwarenessUpdate,
  removeAwarenessStates,
} from "https://esm.sh/y-protocols@1.0.7/awareness?deps=yjs@13.6.33";
import markdownit from "https://esm.sh/markdown-it@14.1.0";

const LOCAL = "local";
const SERVER = "server";
const COLORS = ["#7aa2f7", "#9ece6a", "#f7768e", "#bb9af7", "#7dcfff", "#e0af68"];

const $ = (id) => document.getElementById(id);
const editor = $("editor");
const overlay = $("overlay");
const preview = $("preview");
const previewMode = $("preview-mode");
const statusEl = $("status");
const peersEl = $("peers");
const undoButton = $("undo");
const redoButton = $("redo");
const offlineButton = $("offline");
const saveButton = $("save");

const fileId = decodeURIComponent(location.pathname.split("/").pop());
const userId = localStorage.getItem("v2-user") || crypto.randomUUID();
localStorage.setItem("v2-user", userId);
const color = colorFor(userId);

// The browser keeps its own replica, so editing continues while offline and merges on reconnect.
const ydoc = new Y.Doc();
const ytext = ydoc.getText("md");
const awareness = new Awareness(ydoc);
awareness.setLocalStateField("user", { id: userId, name: shortId(userId), color });
const undoManager = new Y.UndoManager(ytext, { trackedOrigins: new Set([LOCAL]), captureTimeout: 400 });
const md = markdownit({ html: false, breaks: true, linkify: true });
const socket = io({ auth: { file_id: fileId, user_id: userId }, transports: ["websocket"] });

let online = false;
let shadow = "";          // textarea contents as last reconciled with the doc
let selection = null;     // { anchor, head } as relative positions, so it survives remote edits
let selectionBeforeInput = null;
let historyOp = null;     // "undo" | "redo" while the UndoManager is transacting
let lastCursor = "";

// ---------- editing: every input event becomes one transaction on the Y.Text ----------

function applyChange({ start, deleteCount, insert }) {
  ydoc.transact(() => {
    if (deleteCount) ytext.delete(start, deleteCount);
    if (insert) ytext.insert(start, insert);
  }, LOCAL);
}

/** Smallest single edit turning a into b, anchored on the caret so repeated characters resolve sensibly. */
function diff(a, b, caret) {
  let start = 0;
  const max = Math.min(a.length, b.length, caret);
  while (start < max && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > Math.max(start, caret) && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }
  return { start, deleteCount: endA - start, insert: b.slice(start, endB) };
}

// ---------- relative positions ----------

const stickyAt = (offset) => Y.createRelativePositionFromTypeIndex(ytext, offset);

function resolve(relative) {
  if (!relative) return 0;
  const absolute = Y.createAbsolutePositionFromRelativePosition(relative, ydoc);
  return absolute && absolute.type === ytext ? absolute.index : 0;
}

function captureSelection() {
  selection = { anchor: stickyAt(editor.selectionStart), head: stickyAt(editor.selectionEnd) };
}

function publishCursor() {
  if (!selection) return;
  const cursor = {
    anchor: Y.relativePositionToJSON(selection.anchor),
    head: Y.relativePositionToJSON(selection.head),
  };
  const key = JSON.stringify(cursor);
  if (key === lastCursor) return;
  lastCursor = key;
  awareness.setLocalStateField("cursor", cursor);
}

// ---------- painting ----------

function refresh() {
  const text = ytext.toString();
  if (text !== editor.value) {
    const start = resolve(selection?.anchor);
    const end = resolve(selection?.head);
    const scroll = editor.scrollTop;
    editor.value = text;
    if (document.activeElement === editor) editor.setSelectionRange(start, end);
    editor.scrollTop = scroll;
  }
  shadow = text;
  paintOverlay();
  if (!online) renderLocalPreview();
}

function remoteStates() {
  return [...awareness.getStates()].filter(([clientId, state]) => clientId !== ydoc.clientID && state?.user);
}

function paintOverlay() {
  const text = editor.value;
  const marks = [];
  for (const [, state] of remoteStates()) {
    if (!state.cursor?.head) continue;
    try {
      const offset = resolve(Y.createRelativePositionFromJSON(state.cursor.head));
      marks.push([Math.min(offset, text.length), state.user]);
    } catch {
      /* malformed cursor from a peer */
    }
  }
  marks.sort((a, b) => a[0] - b[0]);
  let html = "";
  let at = 0;
  for (const [offset, user] of marks) {
    const c = safeColor(user.color);
    html += escapeHtml(text.slice(at, offset));
    html += `<span class="caret" style="border-color:${c};--c:${c}" data-label="${escapeHtml(user.name ?? shortId(user.id))}"></span>`;
    at = offset;
  }
  overlay.innerHTML = html + escapeHtml(text.slice(at)) + "\n";
  overlay.scrollTop = editor.scrollTop;
}

function paintPeers() {
  const seen = new Map();
  for (const [, state] of remoteStates()) seen.set(state.user.id, state.user);
  peersEl.replaceChildren(...[...seen.values()].map((user) => {
    const dot = document.createElement("span");
    dot.style.background = safeColor(user.color);
    dot.title = user.name ?? shortId(user.id);
    return dot;
  }));
}

function showPreview(html, mode) {
  preview.innerHTML = html;
  previewMode.textContent = mode === "server" ? "" : "offline preview";
}

let previewFrame = 0;
function renderLocalPreview() {
  cancelAnimationFrame(previewFrame);
  previewFrame = requestAnimationFrame(() => showPreview(md.render(ytext.toString()), "local"));
}

function setStatus(text) {
  statusEl.textContent = text ?? (online ? "online" : "offline");
  statusEl.classList.toggle("online", online);
  offlineButton.textContent = socket.connected ? "Go offline" : "Go online";
  saveButton.disabled = !online;
}

function updateHistoryButtons() {
  undoButton.disabled = undoManager.undoStack.length === 0;
  redoButton.disabled = undoManager.redoStack.length === 0;
}

// ---------- undo / redo: only this session's own edits ----------

function undo() {
  historyOp = "undo";
  try { undoManager.undo(); } finally { historyOp = null; }
}

function redo() {
  historyOp = "redo";
  try { undoManager.redo(); } finally { historyOp = null; }
}

undoManager.on("stack-item-added", (event) => {
  event.stackItem.meta.set("selection", event.type === "undo" ? selectionBeforeInput ?? selection : selection);
  updateHistoryButtons();
});
undoManager.on("stack-item-popped", (event) => {
  const restored = event.stackItem.meta.get("selection");
  if (restored) {
    selection = restored;
    editor.setSelectionRange(resolve(restored.anchor), resolve(restored.head));
    publishCursor();
  }
  updateHistoryButtons();
});
undoManager.on("stack-cleared", updateHistoryButtons);

// ---------- doc and awareness observers ----------

ytext.observe((_event, transaction) => {
  if (transaction.origin === LOCAL) paintOverlay();
  else refresh();
});

ydoc.on("update", (update, origin) => {
  if (origin === SERVER || !online) return;
  socket.emit("update", { update, undo: historyOp === "undo", redo: historyOp === "redo" });
});

awareness.on("change", () => {
  paintOverlay();
  paintPeers();
});

// Only our own state goes up; the server relays it to everyone else on this file.
awareness.on("update", ({ added, updated, removed }, origin) => {
  if (origin === SERVER) {
    // Forget removed peers' clocks, or their reconnect (which may re-send the same clock) would be ignored.
    for (const clientId of removed) if (clientId !== ydoc.clientID) awareness.meta.delete(clientId);
    return;
  }
  if (!online) return;
  if ([...added, ...updated, ...removed].includes(ydoc.clientID)) {
    socket.emit("awareness", encodeAwarenessUpdate(awareness, [ydoc.clientID]));
  }
});

// ---------- textarea plumbing ----------

editor.addEventListener("beforeinput", (event) => {
  if (event.inputType === "historyUndo") {
    event.preventDefault();
    undo();
    return;
  }
  if (event.inputType === "historyRedo") {
    event.preventDefault();
    redo();
    return;
  }
  captureSelection();
  selectionBeforeInput = selection;
});

editor.addEventListener("input", () => {
  const next = editor.value;
  const change = diff(shadow, next, editor.selectionEnd);
  shadow = next;
  if (change.deleteCount || change.insert) applyChange(change);
  if (ytext.toString() !== next) refresh();
  captureSelection();
  publishCursor();
});

editor.addEventListener("keydown", (event) => {
  const mod = event.metaKey || event.ctrlKey;
  const key = event.key.toLowerCase();
  if (mod && key === "z") {
    event.preventDefault();
    event.shiftKey ? redo() : undo();
  } else if (mod && key === "y") {
    event.preventDefault();
    redo();
  }
});

for (const type of ["keyup", "mouseup", "select", "focus"]) {
  editor.addEventListener(type, () => {
    captureSelection();
    publishCursor();
  });
}
editor.addEventListener("scroll", () => { overlay.scrollTop = editor.scrollTop; });

undoButton.addEventListener("click", () => { undo(); editor.focus(); });
redoButton.addEventListener("click", () => { redo(); editor.focus(); });

// ---------- socket ----------

socket.on("connect", () => {
  setStatus("syncing");
  socket.emit("sync", Y.encodeStateVector(ydoc), (reply) => {
    if (!reply) return;
    Y.applyUpdate(ydoc, new Uint8Array(reply.update), SERVER);
    if (reply.awareness) applyAwarenessUpdate(awareness, new Uint8Array(reply.awareness), SERVER);
    // Whatever the server lacks (edits made while offline) goes back in one update.
    const missing = Y.encodeStateAsUpdate(ydoc, new Uint8Array(reply.state));
    online = true;
    if (missing.length > 2) socket.emit("update", { update: missing, undo: false });
    socket.emit("awareness", encodeAwarenessUpdate(awareness, [ydoc.clientID]));
    editor.disabled = false;
    refresh();
    showPreview(reply.html, "server");
    setStatus();
  });
});

socket.on("update", (update) => Y.applyUpdate(ydoc, new Uint8Array(update), SERVER));
socket.on("awareness", (update) => applyAwarenessUpdate(awareness, new Uint8Array(update), SERVER));
socket.on("render", ({ html }) => { if (online) showPreview(html, "server"); });

socket.on("disconnect", () => {
  online = false;
  const peers = [...awareness.getStates().keys()].filter((clientId) => clientId !== ydoc.clientID);
  removeAwarenessStates(awareness, peers, SERVER);
  setStatus();
  renderLocalPreview();
});

socket.on("connect_error", (error) => {
  setStatus(error.message === "unknown file" ? "file not found" : "offline");
});

offlineButton.addEventListener("click", () => {
  if (socket.connected) {
    socket.disconnect();
  } else {
    socket.connect();
    setStatus("connecting");
  }
});

saveButton.addEventListener("click", async () => {
  saveButton.disabled = true;
  const response = await fetch(`/api/files/${fileId}`, { method: "PUT" });
  saveButton.textContent = response.ok ? "Saved" : "Save failed";
  setTimeout(() => {
    saveButton.textContent = "Save";
    saveButton.disabled = !online;
  }, 1200);
});

// ---------- utils ----------

function colorFor(id) {
  let hash = 0;
  for (const ch of id) hash = (hash * 31 + ch.charCodeAt(0)) | 0;
  return COLORS[Math.abs(hash) % COLORS.length];
}

function safeColor(value) {
  return /^#[0-9a-f]{6}$/i.test(value ?? "") ? value : "#8f877b";
}

function shortId(id) {
  return String(id ?? "").slice(0, 6);
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
}

window.v2 = { ydoc, ytext, awareness, undoManager, socket };
