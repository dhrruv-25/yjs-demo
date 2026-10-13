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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from helper import parse_doc_to_md_arr, parse_doc_to_md_str, parse_md_str_to_html

ROOT = Path(__file__).resolve().parent
COLORS = ("#7aa2f7", "#9ece6a", "#f7768e", "#bb9af7", "#7dcfff")

DOC = cr.Doc()
DOC["markdown_arr"] = cr.Array()
indexes: dict[str, int] = {}
lock = asyncio.Lock()

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
api = FastAPI()
app = socketio.ASGIApp(sio, api)


def apply_keystroke(op: str, index: int, char: str, count: int = 1) -> None:
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
    color = COLORS[sum(sid.encode()) % len(COLORS)]
    return f'<span class="caret" data-sid="{escape(sid, quote=True)}" style="--c:{color}"></span>'


BR_BEFORE = re.compile(r"<br\s*/?\s*>[ \t]*$", re.IGNORECASE)


def with_line_breaks(text: str) -> tuple[str, list[int]]:
    """Put a <br> before each newline that does not already have one."""
    pieces: list[str] = []
    mapping = [0]
    size = 0
    for index, char in enumerate(text):
        if char == "\n" and not BR_BEFORE.search(text[:index]):
            pieces.append("<br>")
            size += 4
        pieces.append(char)
        size += 1
        mapping.append(size)
    return "".join(pieces), mapping


def html_with_carets(text: str) -> str:
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
    for sid, pos in indexes.items():
        if sid != author and pos >= at:
            indexes[sid] = max(0, pos + delta)


async def broadcast() -> None:
    text = parse_doc_to_md_str(DOC)
    for sid in indexes:
        indexes[sid] = max(0, min(indexes[sid], len(text)))
    await sio.emit(
        "render",
        {"html": html_with_carets(text), "markdown": text, "indexes": dict(indexes)},
    )


@sio.event
async def connect(sid, _environ):
    async with lock:
        indexes[sid] = len(parse_doc_to_md_str(DOC))
        await broadcast()


@sio.event
async def disconnect(sid):
    async with lock:
        indexes.pop(sid, None)
        await broadcast()


@sio.event
async def keystroke(sid, data):
    async with lock:
        if not isinstance(data, dict):
            return {"ok": False}
        try:
            index = int(data.get("index", 0))
        except (TypeError, ValueError):
            return {"ok": False}
        index = max(0, min(index, len(parse_doc_to_md_str(DOC))))
        op = data.get("op")
        char = data.get("char") or ""
        if op == "backspace" and index > 0:
            apply_keystroke("backspace", index, "")
            shift_others(sid, index, -1)
            indexes[sid] = index - 1
        elif op == "delete":
            try:
                count = int(data.get("count", 0))
            except (TypeError, ValueError):
                return {"ok": False}
            count = max(0, min(count, len(parse_doc_to_md_str(DOC)) - index))
            if count <= 0:
                return {"ok": False}
            apply_keystroke("delete", index, "", count)
            shift_for_delete(sid, index, count)
        elif op == "add" and isinstance(char, str) and char:
            apply_keystroke("add", index, char)
            shift_others(sid, index, len(char))
            indexes[sid] = index + len(char)
        else:
            return {"ok": False}
        await broadcast()
        return {"ok": True}


@sio.event
async def cursor(sid, data):
    async with lock:
        if sid not in indexes or not isinstance(data, dict):
            return {"ok": False}
        try:
            index = int(data.get("index", 0))
        except (TypeError, ValueError):
            return {"ok": False}
        text = parse_doc_to_md_str(DOC)
        indexes[sid] = max(0, min(index, len(text)))
        await broadcast()
        return {"ok": True}


@api.get("/")
async def page():
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-store"})


api.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


def lan_address() -> str:
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
