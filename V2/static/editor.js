import * as Y from "https://esm.sh/yjs@13.6.33";
import markdownit from "https://esm.sh/markdown-it@14.1.0";

const LOCAL = "local";
const SERVER = "server";
const CURSOR = "cursor";
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
const lines = ydoc.getArray("md");
const cursors = ydoc.getMap("cursor");
const undoManager = new Y.UndoManager(lines, { trackedOrigins: new Set([LOCAL]), captureTimeout: 400 });
const md = markdownit({ html: false, breaks: true, linkify: true });
const socket = io({ auth: { file_id: fileId, user_id: userId }, transports: ["websocket"] });

let online = false;
let shadow = "";          // textarea contents as last reconciled with the doc
let selection = null;     // caret as sticky positions, so it survives remote edits
let selectionBeforeInput = null;
let historyOp = null;     // "undo" | "redo" while the UndoManager is transacting
let lastCursor = "";

// ---------- line model: Y.Array<Y.Text>, one Y.Text per line ----------

const docText = () => lines.toArray().map(String).join("\n");

function locate(offset) {
  const arr = lines.toArray();
  for (let i = 0; i < arr.length; i++) {
    if (offset <= arr[i].length) return [i, offset];
    offset -= arr[i].length + 1;
  }
  const last = arr.length - 1;
  return [last, last >= 0 ? arr[last].length : 0];
}

function offsetOf(line, col) {
  const arr = lines.toArray();
  let offset = 0;
  for (let i = 0; i < line; i++) offset += arr[i].length + 1;
  return offset + col;
}

function deleteRange(from, to) {
  const [l1, c1] = locate(from);
  const [l2, c2] = locate(to);
  const first = lines.get(l1);
  if (l1 === l2) {
    first.delete(c1, c2 - c1);
    return;
  }
  const tail = lines.get(l2).toString().slice(c2);
  first.delete(c1, first.length - c1);
  if (tail) first.insert(c1, tail);
  lines.delete(l1 + 1, l2 - l1);
}

function insertAt(offset, text) {
  const [l, c] = locate(offset);
  const line = lines.get(l);
  const parts = text.split("\n");
  if (parts.length === 1) {
    line.insert(c, text);
    return;
  }
  const tail = line.toString().slice(c);
  if (tail) line.delete(c, tail.length);
  if (parts[0]) line.insert(c, parts[0]);
  const fresh = parts.slice(1).map((part, i) => new Y.Text(i === parts.length - 2 ? part + tail : part));
  lines.insert(l + 1, fresh);
}

function applyChange({ start, deleteCount, insert }) {
  ydoc.transact(() => {
    if (lines.length === 0) lines.push([new Y.Text()]);
    if (deleteCount) deleteRange(start, start + deleteCount);
    if (insert) insertAt(start, insert);
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

// ---------- sticky positions ----------

function stickyAt(offset) {
  const [line, col] = locate(offset);
  if (line < 0) return null;
  return {
    line: Y.createRelativePositionFromTypeIndex(lines, line),
    col: Y.createRelativePositionFromTypeIndex(lines.get(line), col),
  };
}

function resolve(sticky) {
  if (!sticky) return 0;
  const col = Y.createAbsolutePositionFromRelativePosition(sticky.col, ydoc);
  if (col) {
    const line = lines.toArray().indexOf(col.type);
    if (line >= 0) return offsetOf(line, col.index);
  }
  const line = Y.createAbsolutePositionFromRelativePosition(sticky.line, ydoc);
  if (line && lines.length) return offsetOf(Math.min(line.index, lines.length - 1), 0);
  return 0;
}

function captureSelection() {
  selection = { start: stickyAt(editor.selectionStart), end: stickyAt(editor.selectionEnd) };
}

function publishCursor(force = false) {
  if (!online || !selection?.end) return;
  const line = Y.encodeRelativePosition(selection.end.line);
  const col = Y.encodeRelativePosition(selection.end.col);
  const key = `${socket.id}:${line}:${col}`;
  if (!force && key === lastCursor) return;
  lastCursor = key;
  ydoc.transact(() => cursors.set(socket.id, { user_id: userId, color, line, col }), CURSOR);
}

// ---------- painting ----------

function refresh() {
  const text = docText();
  if (text !== editor.value) {
    const start = resolve(selection?.start);
    const end = resolve(selection?.end);
    const scroll = editor.scrollTop;
    editor.value = text;
    if (document.activeElement === editor) editor.setSelectionRange(start, end);
    editor.scrollTop = scroll;
  }
  shadow = text;
  paintOverlay();
  if (!online) renderLocalPreview();
}

function paintOverlay() {
  const text = editor.value;
  const marks = [];
  cursors.forEach((cursor, sid) => {
    if (sid === socket.id || !cursor?.col || !cursor?.line) return;
    try {
      const offset = resolve({
        line: Y.decodeRelativePosition(new Uint8Array(cursor.line)),
        col: Y.decodeRelativePosition(new Uint8Array(cursor.col)),
      });
      marks.push([Math.min(offset, text.length), cursor]);
    } catch {
      /* malformed cursor from a peer */
    }
  });
  marks.sort((a, b) => a[0] - b[0]);
  let html = "";
  let at = 0;
  for (const [offset, cursor] of marks) {
    const c = safeColor(cursor.color);
    html += escapeHtml(text.slice(at, offset));
    html += `<span class="caret" style="border-color:${c};--c:${c}" data-label="${escapeHtml(shortId(cursor.user_id))}"></span>`;
    at = offset;
  }
  overlay.innerHTML = html + escapeHtml(text.slice(at)) + "\n";
  overlay.scrollTop = editor.scrollTop;
}

function paintPeers() {
  const seen = new Map();
  cursors.forEach((cursor, sid) => {
    if (sid !== socket.id && cursor?.user_id) seen.set(cursor.user_id, safeColor(cursor.color));
  });
  peersEl.replaceChildren(...[...seen].map(([id, c]) => {
    const dot = document.createElement("span");
    dot.style.background = c;
    dot.title = shortId(id);
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
  previewFrame = requestAnimationFrame(() => showPreview(md.render(docText()), "local"));
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
    const start = resolve(restored.start);
    const end = resolve(restored.end);
    editor.setSelectionRange(start, end);
    publishCursor();
  }
  updateHistoryButtons();
});
undoManager.on("stack-cleared", updateHistoryButtons);

// ---------- doc observers ----------

lines.observeDeep((_events, transaction) => {
  if (transaction.origin === LOCAL) paintOverlay();
  else refresh();
});
cursors.observe(() => {
  paintOverlay();
  paintPeers();
});

ydoc.on("update", (update, origin) => {
  if (origin === SERVER || !online) return;
  socket.emit("update", { update, undo: historyOp === "undo", redo: historyOp === "redo" });
});

// ---------- textarea plumbing: every input event becomes one transaction ----------

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
  if (docText() !== next) refresh();
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

// ---------- socket: the only traffic is binary updates ----------

socket.on("connect", () => {
  setStatus("syncing");
  socket.emit("sync", Y.encodeStateVector(ydoc), (reply) => {
    if (!reply) return;
    Y.applyUpdate(ydoc, new Uint8Array(reply.update), SERVER);
    // Whatever the server lacks (edits made while offline) goes back in one update.
    const missing = Y.encodeStateAsUpdate(ydoc, new Uint8Array(reply.state));
    online = true;
    if (missing.length > 2) socket.emit("update", { update: missing, undo: false });
    editor.disabled = false;
    refresh();
    showPreview(reply.html, "server");
    setStatus();
    if (!selection) captureSelection();
    publishCursor(true);
  });
});

socket.on("update", (update) => Y.applyUpdate(ydoc, new Uint8Array(update), SERVER));
socket.on("render", ({ html }) => { if (online) showPreview(html, "server"); });

socket.on("disconnect", () => {
  online = false;
  lastCursor = "";
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

window.v2 = { ydoc, lines, cursors, undoManager, socket };
