"""V2: single-instance collaborative markdown editor.

Run with `python V2/app.py` (from anywhere) or `uvicorn V2.app:app --port 8001` (from the repo root).
"""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

if __package__ in (None, ""):
    # Run as a script: import from the repo root, not V2/, or V2/operations would shadow V1's `operations` package.
    sys.path[0] = str(Path(__file__).resolve().parents[1])

import socketio
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from V2.operations.file_ops import create_file, delete_file as delete_file_from_disk, doc_lines, list_files, read_history
from V2.operations.initialise_docs import Doc_constructor, doc_id_exists
from V2.operations.render import render_doc
from V2.registry import Registry

log = logging.getLogger(__name__)

STATIC = Path(__file__).resolve().parent / "static"
RENDER_DELAY = 0.15
DRAIN_SECONDS = float(os.environ.get("V2_DRAIN_SECONDS", "10"))

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")


async def emit_to(sid: str, event: str, payload):
    await sio.emit(event, payload, to=sid)


registry = Registry(emit_to, drain_seconds=DRAIN_SECONDS)
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
    return network.doc.Doc if network else Doc_constructor(file_id)


def schedule_render(file_id: str):
    """Throttled: at most one render per RENDER_DELAY per file."""
    task = _render_tasks.get(file_id)
    if task is None or task.done():
        _render_tasks[file_id] = asyncio.create_task(_render_later(file_id))


async def _render_later(file_id: str):
    await asyncio.sleep(RENDER_DELAY)
    network = registry.network(file_id)
    if network is not None:
        await sio.emit("render", {"html": network.doc.render()}, room=file_id)


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
    return network.doc.cursor_positions() if network else {}


@api.get("/api/files/{file_id}/history")
async def get_history(file_id: str):
    _require_file(file_id)
    network = registry.network(file_id)
    if network:
        network.drain()
    return read_history(file_id)


@api.put("/api/files/{file_id}")
async def put_file(file_id: str):
    _require_file(file_id)
    saved_live = registry.save(file_id)
    return {"id": file_id, "saved": True, "live": saved_live}


@api.delete("/api/files/{file_id}")
async def delete_file(file_id: str):
    _require_file(file_id)
    if registry.network(file_id):
        raise HTTPException(status_code=409, detail="file is open in an editor")
    delete_file_from_disk(file_id)
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
    """Handshake: return what the client is missing, our state vector (so it can send back what we are missing) and all cursors."""
    network = registry.network_for(sid)
    if network is None:
        return None
    reply = network.sync(sid, bytes(state_vector or b"\x00"))
    reply["html"] = network.doc.render()
    return reply


@sio.event
async def update(sid, data):
    network = registry.network_for(sid)
    if network is None:
        return
    if isinstance(data, dict):
        payload, undo, redo = data.get("update"), bool(data.get("undo")), bool(data.get("redo"))
    else:
        payload, undo, redo = data, False, False
    if not payload:
        return
    network.receive_update(sid, bytes(payload), undo=undo, redo=redo)
    schedule_render(network.file_id)


@sio.event
async def awareness(sid, data):
    network = registry.network_for(sid)
    if network is not None and data:
        network.receive_awareness(sid, bytes(data))


@sio.event
async def disconnect(sid, reason=None):
    file_id = await registry.leave(sid)
    if file_id:
        schedule_render(file_id)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get("V2_HOST", "127.0.0.1"), port=int(os.environ.get("V2_PORT", "8001")))
