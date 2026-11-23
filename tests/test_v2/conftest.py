import asyncio

import pytest
from pycrdt import Awareness, Doc

from V2.operations import initialise_docs
from V2.operations.initialise_docs import Doc_template
from V2.registry import Registry


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def v2_docs(tmp_path, monkeypatch):
    folder = tmp_path / "documents"
    folder.mkdir()
    monkeypatch.setattr(initialise_docs, "docs_dir", folder)
    return folder


class Client:
    """Stands in for a browser: its own replica and awareness, synced through the calls the socket handlers make."""

    def __init__(self, hub: "Hub", user_id: str):
        self.hub = hub
        self.user_id = user_id
        self.doc: Doc = Doc_template()
        self.awareness = Awareness(self.doc)
        self.awareness.set_local_state({"user": {"id": user_id, "name": user_id, "color": "#7aa2f7"}})
        self.sid: str | None = None
        self.received: list[tuple[str, bytes]] = []
        self._connections = 0

    @property
    def network(self):
        return self.hub.registry.network_for(self.sid)

    @property
    def text(self) -> str:
        return str(self.doc["md"])

    @property
    def peers(self) -> dict[int, dict]:
        return {cid: state for cid, state in self.awareness.states.items() if cid != self.doc.client_id}

    def connect(self, file_id: str):
        self._connections += 1
        self.sid = f"{self.user_id}-{self._connections}"
        self.hub.clients[self.sid] = self
        self.hub.registry.join(self.sid, self.user_id, file_id)
        reply = self.network.sync(self.sid, self.doc.get_state())
        self.doc.apply_update(reply["update"])
        if reply["awareness"]:
            self.awareness.apply_awareness_update(reply["awareness"], "server")
        missing = self.doc.get_update(reply["state"])
        if missing != b"\x00\x00":
            self.network.receive_update(self.sid, missing)
        self.publish_awareness()

    async def disconnect(self):
        await self.hub.registry.leave(self.sid)
        self.hub.clients.pop(self.sid)
        self.sid = None
        self.awareness.remove_awareness_states(list(self.peers), "server")
        self.forget_removed_peers()

    def forget_removed_peers(self):
        """Mirrors editor.js: drop clocks of removed peers so their reconnect is accepted."""
        for client_id in list(self.awareness.meta):
            if client_id != self.doc.client_id and client_id not in self.awareness.states:
                del self.awareness.meta[client_id]

    def edit(self, change, undo: bool = False, redo: bool = False):
        before = self.doc.get_state()
        with self.doc.transaction():
            change(self.doc["md"])
        if self.sid is not None:
            self.network.receive_update(self.sid, self.doc.get_update(before), undo=undo, redo=redo)

    def insert(self, index: int, text: str, **kwargs):
        self.edit(lambda md: md.insert(index, text), **kwargs)

    def set_cursor(self, anchor: dict, head: dict | None = None) -> bool:
        self.awareness.set_local_state_field("cursor", {"anchor": anchor, "head": head or anchor})
        return self.publish_awareness()

    def publish_awareness(self, client_ids: list[int] | None = None) -> bool:
        update = self.awareness.encode_awareness_update(client_ids or [self.doc.client_id])
        return self.network.receive_awareness(self.sid, update)


class Hub:
    def __init__(self):
        self.clients: dict[str, Client] = {}
        self.registry = Registry(self._emit)

    async def _emit(self, sid: str, event: str, payload: bytes):
        client = self.clients.get(sid)
        if client is None:
            return
        client.received.append((event, payload))
        if event == "update":
            client.doc.apply_update(payload)
        elif event == "awareness":
            client.awareness.apply_awareness_update(payload, "server")
            client.forget_removed_peers()

    def client(self, user_id: str) -> Client:
        return Client(self, user_id)

    async def settle(self):
        for _ in range(3):
            await asyncio.sleep(0.01)


@pytest.fixture
async def hub(v2_docs):
    hub = Hub()
    yield hub
    await hub.registry.shutdown()
