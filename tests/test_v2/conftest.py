import asyncio

import pytest
from pycrdt import Doc, Text

from V2.operations import initialise_docs
from V2.operations.initialise_docs import Doc_template
from V2.registry import Registry
from V2.schema.InMemoryDoc import CLIENT


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
    """Stands in for a browser: its own replica, synced through the same calls the socket handlers make."""

    def __init__(self, network: "Network", user_id: str):
        self.network = network
        self.user_id = user_id
        self.doc: Doc = Doc_template()
        self.sid: str | None = None
        self._connections = 0

    @property
    def node(self):
        return self.network.registry.docs[self.sid]

    @property
    def lines(self) -> list[str]:
        return [str(line) for line in self.doc["md"]]

    def connect(self, file_id: str):
        self._connections += 1
        self.sid = f"{self.user_id}-{self._connections}"
        self.network.clients[self.sid] = self
        self.network.registry.join(self.sid, self.user_id, file_id)
        update, state = self.node.sync(self.doc.get_state())
        self.doc.apply_update(update)
        missing = self.doc.get_update(state)
        if missing != b"\x00\x00":
            self.node.apply_update(missing, origin=CLIENT)

    async def disconnect(self):
        await self.network.registry.leave(self.sid)
        self.network.clients.pop(self.sid)
        self.sid = None

    def edit(self, change, undo: bool = False):
        before = self.doc.get_state()
        with self.doc.transaction():
            change(self.doc)
        if self.sid is not None:
            self.node.apply_update(self.doc.get_update(before), origin=CLIENT, undo=undo)

    def insert(self, line: int, col: int, text: str, **kwargs):
        self.edit(lambda doc: doc["md"][line].insert(col, text), **kwargs)

    def append_line(self, text: str):
        self.edit(lambda doc: doc["md"].append(Text(text)))


class Network:
    def __init__(self):
        self.clients: dict[str, Client] = {}
        self.registry = Registry(self._emit)

    async def _emit(self, sid: str, update: bytes):
        client = self.clients.get(sid)
        if client is not None:
            client.doc.apply_update(update)

    def client(self, user_id: str) -> Client:
        return Client(self, user_id)

    async def settle(self):
        for _ in range(3):
            for network in list(self.registry.networks.values()):
                await network.settle()
            await asyncio.sleep(0.01)


@pytest.fixture
async def net(v2_docs):
    network = Network()
    yield network
    await network.registry.shutdown()
