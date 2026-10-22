"""One in-memory document, one session. Each connection has its own index."""

import asyncio
import re
import socket
import subprocess
from html import escape
from pathlib import Path

import pycrdt as cr
import socketio
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import logging


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
    )

from controller.sessions_manager import SessionsManager

from schemas.exceptions import SaveDocumentError


log = logging.getLogger(__name__)
logging.basicConfig(filename='example.log', encoding='utf-8', level=logging.INFO)

ROOT = Path(__file__).resolve().parent
COLORS = ("#7aa2f7", "#9ece6a", "#f7768e", "#bb9af7", "#7dcfff")

DOC = cr.Doc()
DOC["markdown_arr"] = cr.Array()
indexes: dict[str, int] = {}
lock = asyncio.Lock()

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
api = FastAPI()
app = socketio.ASGIApp(sio, api)

session_manager = SessionsManager()

def apply_keystroke(op: str, index: int, char: str, count: int = 1) -> None: 
    """
    Applies a keystroke operation to the document.
    """
    arr = parse_doc_to_md_arr(DOC)
    index = max(0, min(index, len(arr)))
    with DOC.transaction():
        if op == "backspace":
            if index > 0:
                del arr[index - 1]
            return
        if op == "delete":
            count = max(0, min(count, len(arr) - index))
            for offset in range(count - 1, -1, -1):
                del arr[index + offset]
            return
        for offset, ch in enumerate(char):
            arr.insert(index + offset, ch)


def shift_for_delete(author: str, start: int, count: int) -> None: 
    """
    Shifts the cursor position of other users after a delete operation.
    * This function edits the index object in session memory. *
    """
    end = start + count
    for sid, pos in indexes.items():
        if sid == author:
            continue
        if pos >= end:
            indexes[sid] = pos - count
        elif pos > start:
            indexes[sid] = start
    indexes[author] = start


def marker(sid: str) -> str:

    """
    Adds a colored caret to the document to indicate the cursor position of each user
    """
    color = COLORS[sum(sid.encode()) % len(COLORS)]
    return f'<span class="caret" data-sid="{escape(sid, quote=True)}" style="--c:{color}"></span>'


BR_BEFORE = re.compile(r"<br\s*/?\s*>[ \t]*$", re.IGNORECASE)       


def with_line_breaks(text: str) -> tuple[str, list[int]]:
    """
    Put a <br> before each newline that does not already have one.
    Renders the 2D page into a 1D line and maps the position of each character by jumping positions in the 1D line at every linebreak.
    """
    pieces: list[str] = []
    mapping = [0]
    size = 0
    for index, char in enumerate(text):
        if char == "\n" and not BR_BEFORE.search(text[:index]): # check if char is /n and whether the text uptill {:index} has a <br> at its end. Basically if a user asks for a new line inline
            pieces.append("<br>")
            size += 4
        pieces.append(char)
        size += 1
        mapping.append(size)
    return "".join(pieces), mapping


def html_with_carets(text: str) -> str:
    """
    Adds carets to the document to indicate the cursor position of each user
    """
    expanded, mapping = with_line_breaks(text)
    chars = list(expanded)
    placed = []
    for sid, pos in indexes.items():
        pos = max(0, min(pos, len(text)))
        placed.append((mapping[pos], sid))
    for at, sid in sorted(placed, reverse=True):
        chars.insert(at, marker(sid))
    return parse_md_str_to_html("".join(chars))


def shift_others(author: str, at: int, delta: int) -> None:
    """
    Shifts the cursor position of other users after a keystroke operation.
    * This function edits the index object in session memory. *
    """
    for sid, pos in indexes.items():
        if sid != author and pos >= at:
            indexes[sid] = max(0, pos + delta)


async def broadcast() -> None:
    """
    Broadcasts the document to all users in the session.
    * This function edits the index object in session memory. *
    """
    text = parse_doc_to_md_str(DOC)
    for sid in indexes:
        indexes[sid] = max(0, min(indexes[sid], len(text)))
    await sio.emit(
        "render",
        {"html": html_with_carets(text), "markdown": text, "indexes": dict(indexes)},
    )


@sio.event
async def connect(sid, _environ):
    """
    Connects a user to the session. initializes the index object in session memory.
    * This function edits the index object in session memory. *
    """
    try:
        await session_manager.get_user_session(sid)
    except FileNotFoundError:
        indexes[sid] = 0

@sio.event
async def disconnect(sid):
    """
    Disconnects a user from the session. removes the index object in session memory.
    * This function edits the index object in session memory. *
    """
    async with lock:
        indexes.pop(sid, None)
        log.info(f"User {sid} disconnected")
        await broadcast()


@sio.event
async def keystroke(sid, data):
    """
    Handles a keystroke operation.
    * This function edits the index object in session memory. *
    """
    return await session_manager.get_user_session(sid).keystroke(sid, data)


@sio.event
async def cursor(sid, data):
    """
    Handles a cursor operation.
    * This function edits the index object in session memory. *
    """
    return await session_manager.get_user_session(sid).cursor(sid, data)


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
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@api.get("/documents/{filename}")
async def document(filename: str):
    """
    Returns the editor page for the requested document.
    Loads document contents into global DOC.
    """
    global DOC
    doc_path = ROOT / "documents" / filename
    if not doc_path.exists() or not doc_path.is_file():
        return {"error": "Document not found"}, 404
    DOC = get_doc_copy_from_path(doc_path)
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-store"})

api.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@api.get("/documents/{filename}/save")
async def save_document(filename: str):
    """
    Saves the document to the file system.
    """
    try:
        await session_manager._get_markdown_session(filename).save_doc()
    except SaveDocumentError:
        return {"error": "Failed to save document"}, 500
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
