from __future__ import annotations

from functools import partial
from typing import Any, Awaitable, Callable

from V2.schema.RelayNetwork import RelayNetwork


class Registry:
    """Single-instance bookkeeping: one RelayNetwork (and so one server doc) per open file."""

    def __init__(self, emit: Callable[[str, str, Any], Awaitable[None]], drain_seconds: float = 0) -> None:
        self._emit = emit
        self._drain_seconds = drain_seconds
        self.networks: dict[str, RelayNetwork] = {}
        self._files: dict[str, str] = {}

    def join(self, sid: str, user_id: str, file_id: str) -> RelayNetwork:
        network = self.networks.get(file_id)
        if network is None:
            network = RelayNetwork(file_id, self._drain_seconds)
            network.start()
            self.networks[file_id] = network
        network.join(sid, user_id, partial(self._emit, sid))
        self._files[sid] = file_id
        return network

    def network_for(self, sid: str) -> RelayNetwork | None:
        file_id = self._files.get(sid)
        return self.networks.get(file_id) if file_id else None

    def network(self, file_id: str) -> RelayNetwork | None:
        return self.networks.get(file_id)

    async def leave(self, sid: str) -> str | None:
        file_id = self._files.pop(sid, None)
        if file_id is None:
            return None
        network = self.networks[file_id]
        network.leave(sid)
        if not network.sessions:
            await network.stop()
            del self.networks[file_id]
        return file_id

    def save(self, file_id: str) -> bool:
        network = self.networks.get(file_id)
        if network is None:
            return False
        network.save()
        return True

    async def shutdown(self):
        for sid in list(self._files):
            await self.leave(sid)
