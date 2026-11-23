from __future__ import annotations

import asyncio
import contextlib
import logging

from pycrdt import Subscription

from V2.schema.InMemoryDoc import InMemoryDoc
from V2.operations.network_ops import publish, pull, _make_observer, run

log = logging.getLogger(__name__)


class RelayNetwork:
    def __init__(self, file_id: str, drain_seconds: float = 0) -> None:
        self.file_id = file_id
        self.nodes: dict[str, InMemoryDoc] = {}
        self._relay_queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
        self._subscriptions: dict[str, Subscription] = {}
        self._drain_seconds = drain_seconds
        self._tasks: list[asyncio.Task] = []

    def join_network(self, MemDoc: InMemoryDoc):
        first = not self.nodes
        self.nodes[MemDoc.sid] = MemDoc
        if first:
            # Cursors persisted by a previous run belong to sessions that no longer exist.
            MemDoc.clear_cursors()
        else:
            pull(MemDoc.sid, self)
        self._subscriptions[MemDoc.sid] = MemDoc.observe(_make_observer(MemDoc.sid, self))

    def leave_network(self, MemDoc_id: str) -> InMemoryDoc | None:
        doc = self.nodes.get(MemDoc_id)
        if doc is None:
            return None
        doc.remove_cursor(MemDoc_id)
        doc.flush()
        doc.Doc.unobserve(self._subscriptions.pop(MemDoc_id))
        self.nodes.pop(MemDoc_id)
        doc.close()
        return doc

    def publish(self, sender: str, update: bytes):
        publish(sender, self, update)

    def any_node(self) -> InMemoryDoc | None:
        return next(iter(self.nodes.values()), None)

    async def relay(self):
        await run(self)

    async def _drain_loop(self):
        while True:
            await asyncio.sleep(self._drain_seconds)
            for node in list(self.nodes.values()):
                node.drain()

    def start(self):
        self._tasks.append(asyncio.create_task(self.relay()))
        if self._drain_seconds > 0:
            self._tasks.append(asyncio.create_task(self._drain_loop()))

    async def stop(self):
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    async def settle(self):
        """Wait until every queued update has been applied to its peer."""
        await self._relay_queue.join()

    async def save(self):
        for node in list(self.nodes.values()):
            node.drain()
        await self.settle()
        node = self.any_node()
        if node is not None:
            node.save()
