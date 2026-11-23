import asyncio

import httpx
import pytest
import socketio
from pycrdt import Awareness

import V2.app as app_module
from V2.operations.file_ops import create_file
from V2.operations.initialise_docs import Doc_template
from V2.registry import Registry

pytestmark = pytest.mark.anyio


@pytest.fixture
async def server(v2_docs, monkeypatch):
    """The real socket handlers, with sio.emit recorded instead of sent."""
    sent: list[tuple] = []

    async def emit(event, data=None, to=None, room=None, **_):
        sent.append((event, data, to or room))

    async def enter_room(sid, room, **_):
        pass

    monkeypatch.setattr(app_module.sio, "emit", emit)
    monkeypatch.setattr(app_module.sio, "enter_room", enter_room)
    registry = Registry(app_module.emit_to)
    monkeypatch.setattr(app_module, "registry", registry)
    monkeypatch.setattr(app_module, "RENDER_DELAY", 0)
    yield sent
    await registry.shutdown()


@pytest.fixture
async def http(v2_docs):
    transport = httpx.ASGITransport(app=app_module.api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def connect(sid, file_id):
    await app_module.connect(sid, {}, {"file_id": file_id, "user_id": sid})
    reply = await app_module.sync(sid, b"\x00")
    doc = Doc_template()
    doc.apply_update(reply["update"])
    return doc, reply


def sent_to(sent, event, target):
    return [data for e, data, to in sent if e == event and to == target]


async def test_socket_handlers_relay_updates_cursors_and_render(server):
    file_id = create_file()
    a_doc, _ = await connect("a", file_id)
    b_doc, _ = await connect("b", file_id)

    before = a_doc.get_state()
    a_doc["md"].insert(0, "**bold**")
    await app_module.update("a", {"update": a_doc.get_update(before), "undo": False})

    a_awareness = Awareness(a_doc)
    a_awareness.set_local_state({"user": {"id": "a"}, "cursor": {"anchor": {"tname": "md"}, "head": {"tname": "md"}}})
    await app_module.awareness("a", a_awareness.encode_awareness_update([a_doc.client_id]))
    await asyncio.sleep(0.05)

    for update in sent_to(server, "update", "b"):
        b_doc.apply_update(update)
    assert str(b_doc["md"]) == "**bold**"
    assert sent_to(server, "update", "a") == []

    b_awareness = Awareness(b_doc)
    for update in sent_to(server, "awareness", "b"):
        b_awareness.apply_awareness_update(update, "server")
    assert b_awareness.states[a_doc.client_id]["user"] == {"id": "a"}

    renders = sent_to(server, "render", file_id)
    assert renders and "<strong>bold</strong>" in renders[-1]["html"]

    _, late = await connect("c", file_id)
    assert late["awareness"] and "<strong>bold</strong>" in late["html"]

    for sid in ("a", "b", "c"):
        await app_module.disconnect(sid)
    assert app_module.registry.networks == {}


async def test_connect_refuses_unknown_file(server):
    with pytest.raises(socketio.exceptions.ConnectionRefusedError):
        await app_module.connect("x", {}, {"file_id": "missing"})


async def test_rest_crud_render_and_history(server, http):
    created = await http.post("/api/files")
    assert created.status_code == 201
    file_id = created.json()["id"]

    listing = (await http.get("/api/files")).json()
    assert [f["id"] for f in listing] == [file_id]

    doc, _ = await connect("a", file_id)
    before = doc.get_state()
    doc["md"].insert(0, "# hi")
    await app_module.update("a", {"update": doc.get_update(before)})

    assert (await http.get(f"/api/files/{file_id}")).json()["lines"] == ["# hi"]
    assert "<h1>hi</h1>" in (await http.get(f"/api/files/{file_id}/render")).json()["html"]
    history = (await http.get(f"/api/files/{file_id}/history")).json()
    assert [entry["transactions"] for entry in history] == [1]
    assert (await http.put(f"/api/files/{file_id}")).json() == {"id": file_id, "saved": True, "live": True}
    assert (await http.delete(f"/api/files/{file_id}")).status_code == 409

    await app_module.disconnect("a")
    assert (await http.get(f"/files/{file_id}")).status_code == 200
    assert (await http.get("/files/nope")).status_code == 404
    assert (await http.get("/api/files/..%2F..%2Fetc")).status_code == 404
    assert (await http.delete(f"/api/files/{file_id}")).status_code == 200
    assert (await http.get("/api/files")).json() == []
    assert list(app_module.registry.networks) == []
