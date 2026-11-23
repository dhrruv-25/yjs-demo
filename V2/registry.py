from __future__ import annotations

from functools import partial
from typing import Awaitable, Callable

from V2.schema.InMemoryDoc import InMemoryDoc
from V2.schema.RelayNetwork import RelayNetwork


class Registry:
    """Single-instance bookkeeping: one RelayNetwork per open file, one InMemoryDoc per socket."""

    def __init__(self, emit_update: Callable[[str, bytes], Awaitable[None]], drain_seconds: float = 0) -> None:
        self._emit_update = emit_update
        self._drain_seconds = drain_seconds
        self.networks: dict[str, RelayNetwork] = {}
        self.docs: dict[str, InMemoryDoc] = {}

    def join(self, sid: str, user_id: str, file_id: str) -> InMemoryDoc:
        network = self.networks.get(file_id)
        if network is None:
            network = RelayNetwork(file_id, self._drain_seconds)
            network.start()
            self.networks[file_id] = network
        doc = InMemoryDoc(sid, user_id, file_id, emit=partial(self._emit_update, sid))
        network.join_network(doc)
        self.docs[sid] = doc
        return doc

    async def leave(self, sid: str) -> str | None:
        doc = self.docs.pop(sid, None)
        if doc is None:
            return None
        network = self.networks[doc.file_id]
        network.leave_network(sid)
        if not network.nodes:
            await network.stop()
            del self.networks[doc.file_id]
        return doc.file_id

    def network(self, file_id: str) -> RelayNetwork | None:
        return self.networks.get(file_id)

    async def save(self, file_id: str) -> bool:
        network = self.networks.get(file_id)
        if network is None:
            return False
        await network.save()
        return True

    async def shutdown(self):
        for sid in list(self.docs):
            await self.leave(sid)
