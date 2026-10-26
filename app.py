"""One in-memory document, one session. Each connection has its own index."""

import asyncio
import re
import socket
import subprocess
from html import escape
from pathlib import Path
from http.cookies import SimpleCookie
import pycrdt as cr
import socketio
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import logging
from bson import ObjectId


from operations.helper import (
    handle_doc_changes,
    parse_doc_to_md_arr,
    parse_md_arr_to_md_str,
    parse_doc_to_md_str,
    parse_md_str_to_html,
    parse_doc_to_html,
    parse_html_to_md,
    parse_md_str_to_doc,
    parse_html_to_doc,
    get_doc_copy_from_path,
    save_doc_to_path,
    get_title_from_path,
    delete_doc_from_path,
    create_doc_from_path,
    set_title_to_doc_from_path,
    )

from controller.sessions_manager import SessionsManager

from schemas.exceptions import (
    SaveDocumentError, 
    UserAlreadyConnectedError,
    UserNotConnectedError,
    SessionNotFoundError
)


log = logging.getLogger(__name__)
logging.basicConfig(filename='example.log', encoding='utf-8', level=logging.INFO)

ROOT = Path(__file__).resolve().parent
COLORS = ("#7aa2f7", "#9ece6a", "#f7768e", "#bb9af7", "#7dcfff")

DOC = cr.Doc()
DOC["markdown_arr"] = cr.Array()
indexes: dict[str, int] = {}
cookies_dict = {}
lock = asyncio.Lock()

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
api = FastAPI()
app = socketio.ASGIApp(sio, api)

session_manager = SessionsManager()

@sio.event
async def connect(sid, _environ):
    """
    Connects a user to the session. initializes the index object in session memory.
    * This function edits the index object in session memory.*
    """
    try:
        cookie = SimpleCookie(_environ.get("HTTP_COOKIE"))
        cookies_dict[sid] = cookie.get("session_id")
        filename = ObjectId(Path(_environ.get("HTTP_REFERER", "")).name.split('.crdt')[0])
        await sio.emit("render", await (await session_manager.connect_user(sid, filename)).broadcast())
        print('connected', sid)
        return {"ok": True}
    except UserAlreadyConnectedError:
        return False 
    except FileNotFoundError:
        return False


@sio.event
async def disconnect(sid):
    """
    Disconnects a user from the session. removes the index object in session memory.
    * This function edits the index object in session memory. *
    """
    async with lock:
        cookies_dict.pop(sid, None)
        log.info(f"User {sid} disconnected")
        final_state = await (await session_manager.get_user_session(sid)).broadcast()
        await session_manager.drop_user_session(sid)
        await sio.emit("render", final_state)
        print('disconnect', sid)
        return {"ok": True}


@sio.event
async def keystroke(sid, data):
    """
    Handles a keystroke operation.
    * This function edits the index object in session memory. *
    """

    try:
        emit = await (await session_manager.get_user_session(sid)).keystroke(sid, data)
    except Exception as e:
        print('error', e)
        return {"ok": False}
    if not emit or emit.get("ok") is False:
        return {"ok": False}

    await sio.emit("render", emit)
    print('emit', emit)
    return {"ok": True}


@sio.event
async def title_keystroke(sid, data):
    """
    Handles a keystroke in the document title.
    Moves the author from the document cursor to title_index.
    """
    try:
        emit = await (await session_manager.get_user_session(sid)).title_keystroke(sid, data)
    except Exception as e:
        print('error', e)
        return {"ok": False}
    if not emit or emit.get("ok") is False:
        return {"ok": False}

    await sio.emit("render", emit)
    print('emit', emit)
    return {"ok": True}


@sio.event
async def cursor(sid, data):
    """
    Handles a cursor operation.
    * This function edits the index object in session memory. *
    """
    try:
        emit = await (await session_manager.get_user_session(sid)).cursor(sid, data)
    except Exception as e:
        print('error', e)
        return {"ok": False}
    if not emit or emit.get("ok") is False:
        return {"ok": False}

    await sio.emit("render", emit)
    print('emit', emit)
    return {"ok": True}


@api.get("/")
async def page():
    """
    Returns the index.html file.
    """
    folder = ROOT / "documents"
    items = [
        f'<li><a href="/documents/{escape(path.name)}">{escape(get_title_from_path(str(path)))}</a></li>'
        for path in sorted(folder.iterdir())
        if path.is_file()
    ]
    html = (ROOT / "static" / "docs-dir.html").read_text().replace("<!--FILES-->", "\n".join(items))
    response = HTMLResponse(html, headers={"Cache-Control": "no-store"})
    response.set_cookie(
        key="session_id",
        value=str(ObjectId()),
        httponly=True,   # JavaScript can't read it
        secure=True,     # Only sent over HTTPS
        samesite="lax",  # Limits cross-site sending
        max_age=3600,    # Lifetime in seconds
    )
    return response


@api.get("/documents/{filename}")
async def document(filename: str):
    """
    Returns the editor page for the requested document.
    Loads document contents into global DOC.
    """
    global DOC
    doc_path = ROOT / "documents" / filename
    if not doc_path.exists() or not doc_path.is_file():
        return JSONResponse({"error": "Document not found"}, status_code=404)
    DOC = get_doc_copy_from_path(doc_path)
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-store"})

api.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@api.post("/documents/{filename}/save")
async def save_document(filename: str):
    """
    Saves the document to the file system.
    """
    try:
        await (await session_manager._get_markdown_session(ObjectId(filename))).save_doc()
    except SaveDocumentError:
        return JSONResponse({"error": "Failed to save document"}, status_code=500)
    except SessionNotFoundError:
        return JSONResponse({"error": "Document not found"}, status_code=404)
    return {"ok": True}


@api.delete("/documents/{filename}")
async def delete_document(filename: str):
    """
    Deletes the document from the file system.
    """
    filename = f'{filename}.crdt'
    delete_doc_from_path(str(ROOT / "documents" / filename))
    return {"ok": True}


@api.put("/create-file/{filename}/{title}")
async def create_document(filename: str, title: str):
    """
    Creates a new document in the file system.
    """
    filename = f'{filename}.crdt'
    create_doc_from_path(str(ROOT / "documents" / filename))
    set_title_to_doc_from_path(str(ROOT / "documents" / filename), title)
    return {"ok": True}


@api.post("/set-title/{filename}/{title}")
async def set_title(filename: str, title: str):
    """
    Sets the title of the document in the file system.
    """
    filename = f'{filename}.crdt'
    set_title_to_doc_from_path(str(ROOT / "documents" / filename), title)
    return {"ok": True}


def lan_address() -> str:
    """
    Returns the LAN address of the device.
    """
    for name in ("en0", "en1"):
        try:
            address = subprocess.check_output(["ipconfig", "getifaddr", name], text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            continue
        if address:
            return address
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "0.0.0.0"


if __name__ == "__main__":
    import uvicorn

    print("Editor running at http://127.0.0.1:5050")
    print(f"On this network: http://{lan_address()}:5050")
    uvicorn.run(app, host="0.0.0.0", port=5050)
