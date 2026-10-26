import asyncio
from urllib.parse import quote

import httpx
import pytest
from bson import ObjectId

import app as app_module
from controller.sessions_manager import SessionsManager
from operations.helper import get_doc_copy_from_path, get_title_from_path, parse_doc_to_md_str
from tests.conftest import write_doc


@pytest.fixture
def client(app_docs, monkeypatch):
    async def _request(method, path, **kwargs):
        app_module.session_manager = SessionsManager()
        app_module.lock = asyncio.Lock()
        transport = httpx.ASGITransport(app=app_module.api)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            return await http.request(method, path, **kwargs)

    return _request


def test_directory_lists_titles_and_sets_a_session_cookie(app_docs, client):
    file_id = write_doc(app_docs, text="body", title="Meeting notes")

    response = asyncio.run(client("GET", "/"))

    assert response.status_code == 200
    assert f'/documents/{file_id}.crdt' in response.text
    assert "Meeting notes" in response.text
    assert 'aria-label", "Edit"' in response.text
    assert "/set-title/" in response.text
    assert response.cookies.get("session_id")


def test_directory_is_empty_without_documents(app_docs, client):
    response = asyncio.run(client("GET", "/"))

    assert response.status_code == 200
    assert "<li>" not in response.text
    assert "<!--FILES-->" not in response.text


def test_open_document_returns_the_editor(app_docs, client):
    file_id = write_doc(app_docs, text="# Hello", title="Hello")

    response = asyncio.run(client("GET", f"/documents/{file_id}.crdt"))

    assert response.status_code == 200
    assert 'id="editor"' in response.text
    assert 'id="save"' in response.text


def test_open_missing_document_is_not_found(app_docs, client):
    response = asyncio.run(client("GET", f"/documents/{ObjectId()}.crdt"))

    assert response.status_code == 404
    assert response.json() == {"error": "Document not found"}


def test_create_set_title_and_delete_roundtrip(app_docs, client):
    async def scenario():
        file_id = ObjectId()
        created = await client("PUT", f"/create-file/{file_id}/{quote('Draft title')}")
        assert created.status_code == 200
        assert created.json() == {"ok": True}
        path = app_docs / f"{file_id}.crdt"
        assert path.is_file()
        assert parse_doc_to_md_str(get_doc_copy_from_path(path)) == ""
        assert get_title_from_path(path) == "Draft title"

        renamed = await client("POST", f"/set-title/{file_id}/{quote('Final title')}")
        assert renamed.status_code == 200
        assert get_title_from_path(path) == "Final title"

        deleted = await client("DELETE", f"/documents/{file_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"ok": True}
        assert not path.exists()

        missing = await client("DELETE", f"/documents/{ObjectId()}")
        assert missing.status_code == 200

    asyncio.run(scenario())


def test_save_writes_the_open_session(app_docs):
    async def scenario():
        file_id = write_doc(app_docs, text="draft", title="Draft")
        app_module.session_manager = SessionsManager()
        session = await app_module.session_manager.connect_user("alice", file_id)
        await session.keystroke("alice", {"op": "add", "index": 5, "char": "!"})

        transport = httpx.ASGITransport(app=app_module.api)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            response = await http.post(f"/documents/{file_id}/save")

        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert parse_doc_to_md_str(get_doc_copy_from_path(app_docs / f"{file_id}.crdt")) == "draft!"

    asyncio.run(scenario())


def test_save_missing_session_is_not_found(app_docs):
    async def scenario():
        app_module.session_manager = SessionsManager()
        transport = httpx.ASGITransport(app=app_module.api)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            response = await http.post(f"/documents/{ObjectId()}/save")
        assert response.status_code == 404
        assert response.json() == {"error": "Document not found"}

    asyncio.run(scenario())


def test_save_failure_is_a_server_error(app_docs, monkeypatch):
    async def scenario():
        file_id = write_doc(app_docs, text="draft")
        app_module.session_manager = SessionsManager()
        await app_module.session_manager.connect_user("alice", file_id)

        def explode(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("controller.markdown_session.save_doc_to_path", explode)
        transport = httpx.ASGITransport(app=app_module.api)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            response = await http.post(f"/documents/{file_id}/save")

        assert response.status_code == 500
        assert response.json() == {"error": "Failed to save document"}

    asyncio.run(scenario())


def test_socket_connect_renders_and_disconnect_saves_when_last(app_docs, monkeypatch):
    async def scenario():
        file_id = write_doc(app_docs, text="socket", title="Live")
        app_module.session_manager = SessionsManager()
        app_module.lock = asyncio.Lock()
        rendered = []

        async def capture(event, data, **_kwargs):
            rendered.append((event, data))

        monkeypatch.setattr(app_module.sio, "emit", capture)
        referer = f"http://127.0.0.1:5050/documents/{file_id}.crdt"
        environ = {"HTTP_COOKIE": "session_id=abc", "HTTP_REFERER": referer}

        assert await app_module.connect("sid-1", environ) == {"ok": True}
        assert rendered[-1][0] == "render"
        assert rendered[-1][1]["markdown"] == "socket"
        assert "sid-1" in rendered[-1][1]["indexes"]

        session = await app_module.session_manager.get_user_session("sid-1")
        await session.keystroke("sid-1", {"op": "add", "index": 6, "char": "!"})

        assert await app_module.disconnect("sid-1") == {"ok": True}
        assert app_module.session_manager.sessions == {}
        assert parse_doc_to_md_str(get_doc_copy_from_path(app_docs / f"{file_id}.crdt")) == "socket!"
        assert rendered[-1][0] == "render"

    asyncio.run(scenario())


def test_socket_keystroke_and_cursor_emit_render(app_docs, monkeypatch):
    async def scenario():
        file_id = write_doc(app_docs, text="ab")
        app_module.session_manager = SessionsManager()
        rendered = []

        async def capture(event, data, **_kwargs):
            rendered.append((event, data))

        monkeypatch.setattr(app_module.sio, "emit", capture)
        referer = f"http://127.0.0.1:5050/documents/{file_id}.crdt"
        await app_module.connect("sid-1", {"HTTP_COOKIE": "session_id=abc", "HTTP_REFERER": referer})

        assert await app_module.keystroke("sid-1", {"op": "add", "index": 2, "char": "c"}) == {"ok": True}
        assert rendered[-1][1]["markdown"] == "abc"
        assert await app_module.cursor("sid-1", {"index": 1}) == {"ok": True}
        assert rendered[-1][1]["indexes"]["sid-1"] == 1
        before = len(rendered)
        assert await app_module.keystroke("sid-1", {"op": "nope"}) == {"ok": False}
        assert len(rendered) == before
        assert rendered[-1][1]["markdown"] == "abc"

    asyncio.run(scenario())


def test_socket_title_keystroke_switches_off_the_document_cursor(app_docs, monkeypatch):
    async def scenario():
        file_id = write_doc(app_docs, text="ab", title="Note")
        app_module.session_manager = SessionsManager()
        rendered = []

        async def capture(event, data, **_kwargs):
            rendered.append((event, data))

        monkeypatch.setattr(app_module.sio, "emit", capture)
        referer = f"http://127.0.0.1:5050/documents/{file_id}.crdt"
        await app_module.connect("sid-1", {"HTTP_COOKIE": "session_id=abc", "HTTP_REFERER": referer})

        assert await app_module.title_keystroke("sid-1", {"op": "add", "index": 4, "char": "s"}) == {"ok": True}
        assert rendered[-1][1]["title"] == "Notes"
        assert rendered[-1][1]["title_index"]["sid-1"] == 5
        assert rendered[-1][1]["indexes"]["sid-1"] is None
        assert await app_module.title_keystroke("sid-1", {"op": "nope"}) == {"ok": False}

    asyncio.run(scenario())


def test_socket_connect_rejects_a_second_connection_and_a_missing_file(app_docs, monkeypatch):
    async def scenario():
        file_id = write_doc(app_docs, text="ab")
        app_module.session_manager = SessionsManager()

        async def capture(*_args, **_kwargs):
            return None

        monkeypatch.setattr(app_module.sio, "emit", capture)
        referer = f"http://127.0.0.1:5050/documents/{file_id}.crdt"
        environ = {"HTTP_COOKIE": "session_id=abc", "HTTP_REFERER": referer}

        assert await app_module.connect("sid-1", environ) == {"ok": True}
        assert await app_module.connect("sid-1", environ) is False

        missing = f"http://127.0.0.1:5050/documents/{ObjectId()}.crdt"
        assert await app_module.connect("sid-2", {"HTTP_COOKIE": "", "HTTP_REFERER": missing}) is False

    asyncio.run(scenario())
