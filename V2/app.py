"""V2: single-instance collaborative markdown editor. Run with `uvicorn V2.app:app --port 8001`."""

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import socketio
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from V2.operations.file_ops import create_file, delete_doc_from_path, doc_lines, list_files
from V2.operations.initialise_docs import Doc_constructor, doc_id_exists, doc_path
from V2.operations.render import render_doc
from V2.registry import Registry
from V2.schema.InMemoryDoc import CLIENT

log = logging.getLogger(__name__)

STATIC = Path(__file__).resolve().parent / "static"
RENDER_DELAY = 0.15
DRAIN_SECONDS = float(os.environ.get("V2_DRAIN_SECONDS", "10"))

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")


async def emit_update(sid: str, update: bytes):
    await sio.emit("update", update, to=sid)


registry = Registry(emit_update, drain_seconds=DRAIN_SECONDS)
_render_tasks: dict[str, asyncio.Task] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    for task in _render_tasks.values():
        task.cancel()
    await registry.shutdown()


api = FastAPI(lifespan=lifespan)
api.mount("/static", StaticFiles(directory=STATIC), name="static")
app = socketio.ASGIApp(sio, other_asgi_app=api)


def _require_file(file_id: str):
    if not doc_id_exists(file_id):
        raise HTTPException(status_code=404, detail="file not found")


def _current_doc(file_id: str):
    network = registry.network(file_id)
    node = network.any_node() if network else None
    return node.Doc if node else Doc_constructor(file_id)


def schedule_render(file_id: str):
    """Throttled: at most one render per RENDER_DELAY per file."""
    task = _render_tasks.get(file_id)
    if task is None or task.done():
        _render_tasks[file_id] = asyncio.create_task(_render_later(file_id))


async def _render_later(file_id: str):
    await asyncio.sleep(RENDER_DELAY)
    network = registry.network(file_id)
    if network is None:
        return
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(network.settle(), timeout=1)
    node = network.any_node()
    if node is not None:
        await sio.emit("render", {"html": node.render()}, room=file_id)


# ---------- pages ----------

@api.get("/")
async def files_page():
    return FileResponse(STATIC / "files.html")


@api.get("/files/{file_id}")
async def editor_page(file_id: str):
    _require_file(file_id)
    return FileResponse(STATIC / "editor.html")


# ---------- REST ----------

@api.get("/api/files")
async def get_files():
    return list_files()


@api.post("/api/files", status_code=201)
async def post_file():
    return {"id": create_file()}


@api.get("/api/files/{file_id}")
async def get_file(file_id: str):
    _require_file(file_id)
    lines = doc_lines(_current_doc(file_id))
    return {"id": file_id, "lines": lines, "markdown": "\n".join(lines)}


@api.get("/api/files/{file_id}/render")
async def get_render(file_id: str):
    _require_file(file_id)
    return {"id": file_id, "html": render_doc(_current_doc(file_id))}


@api.get("/api/files/{file_id}/cursors")
async def get_cursors(file_id: str):
    _require_file(file_id)
    network = registry.network(file_id)
    node = network.any_node() if network else None
    return node.cursor_positions() if node else {}


@api.put("/api/files/{file_id}")
async def put_file(file_id: str):
    _require_file(file_id)
    saved_live = await registry.save(file_id)
    return {"id": file_id, "saved": True, "live": saved_live}


@api.delete("/api/files/{file_id}")
async def delete_file(file_id: str):
    _require_file(file_id)
    if registry.network(file_id):
        raise HTTPException(status_code=409, detail="file is open in an editor")
    delete_doc_from_path(doc_path(file_id))
    return {"id": file_id, "deleted": True}


# ---------- sockets ----------

@sio.event
async def connect(sid, environ, auth):
    auth = auth or {}
    file_id = auth.get("file_id")
    if not doc_id_exists(file_id):
        raise socketio.exceptions.ConnectionRefusedError("unknown file")
    registry.join(sid, auth.get("user_id") or sid, file_id)
    await sio.enter_room(sid, file_id)


@sio.event
async def sync(sid, state_vector):
    """Handshake: return what the client is missing plus our state vector, so it can send back what we are missing."""
    doc = registry.docs.get(sid)
    if doc is None:
        return None
    update, state = doc.sync(bytes(state_vector or b"\x00"))
    return {"update": update, "state": state, "html": doc.render()}


@sio.event
async def update(sid, data):
    doc = registry.docs.get(sid)
    if doc is None:
        return
    if isinstance(data, dict):
        payload, undo, redo = data.get("update"), bool(data.get("undo")), bool(data.get("redo"))
    else:
        payload, undo, redo = data, False, False
    if not payload:
        return
    doc.apply_update(bytes(payload), origin=CLIENT, undo=undo, redo=redo)
    schedule_render(doc.file_id)


@sio.event
async def disconnect(sid, reason=None):
    file_id = await registry.leave(sid)
    if file_id:
        schedule_render(file_id)
