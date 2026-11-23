import asyncio

import httpx
import pytest
from pycrdt import Map

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
    registry = Registry(app_module.emit_update)
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
    return doc


async def test_socket_handlers_relay_updates_and_render(server):
    file_id = create_file()
    a_doc = await connect("a", file_id)
    b_doc = await connect("b", file_id)

    before = a_doc.get_state()
    a_doc["md"][0].insert(0, "**bold**")
    await app_module.update("a", {"update": a_doc.get_update(before), "undo": False})
    await asyncio.sleep(0.05)

    updates_to_b = [data for event, data, to in server if event == "update" and to == "b"]
    assert updates_to_b
    for update in updates_to_b:
        b_doc.apply_update(update)
    assert str(b_doc["md"][0]) == "**bold**"
    assert not [1 for event, _, to in server if event == "update" and to == "a"]

    renders = [data for event, data, room in server if event == "render" and room == file_id]
    assert renders and "<strong>bold</strong>" in renders[-1]["html"]

    await app_module.disconnect("a")
    await app_module.disconnect("b")
    assert app_module.registry.networks == {}


async def test_connect_refuses_unknown_file(server):
    import socketio
    with pytest.raises(socketio.exceptions.ConnectionRefusedError):
        await app_module.connect("x", {}, {"file_id": "missing"})


async def test_rest_crud_and_render(http):
    created = await http.post("/api/files")
    assert created.status_code == 201
    file_id = created.json()["id"]

    listing = (await http.get("/api/files")).json()
    assert [f["id"] for f in listing] == [file_id]

    file = (await http.get(f"/api/files/{file_id}")).json()
    assert file["lines"] == [""]
    assert (await http.get(f"/api/files/{file_id}/render")).json()["html"] == ""
    assert (await http.put(f"/api/files/{file_id}")).json()["saved"] is True
    assert (await http.get(f"/files/{file_id}")).status_code == 200
    assert (await http.get("/files/nope")).status_code == 404
    assert (await http.get("/api/files/..%2F..%2Fetc")).status_code == 404

    assert (await http.delete(f"/api/files/{file_id}")).status_code == 200
    assert (await http.get("/api/files")).json() == []


async def test_delete_refused_while_open(server, http):
    file_id = create_file()
    await connect("a", file_id)
    assert (await http.delete(f"/api/files/{file_id}")).status_code == 409
    await app_module.disconnect("a")
    assert (await http.delete(f"/api/files/{file_id}")).status_code == 200
