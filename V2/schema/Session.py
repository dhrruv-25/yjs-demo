from __future__ import annotations

import asyncio
import base64
import itertools
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable

from pycrdt import merge_updates

log = logging.getLogger(__name__)

Emitter = Callable[[str, Any], Awaitable[None]]


@dataclass
class TransactionDTO:
    update: bytes
    time: datetime
    user_id: str
    sid: str
    undo: bool
    redo: bool = False


class Session:
    """One socket on a file: its transaction buffer, its awareness client ids and its outbound channel."""

    def __init__(self, sid: str, user_id: str, emit: Emitter | None = None) -> None:
        self.sid = sid
        self.user_id = user_id
        self.client_ids: set[int] = set()
        self._emit = emit
        self._buffer: list[TransactionDTO] = []
        # Nothing is pushed before the sync handshake; anything earlier arrives in the handshake reply.
        self.ready = False
        self._outbox: list[bytes] = []
        self._flushing = False
        self._tasks: set[asyncio.Task] = set()

    def log_transaction(self, update: bytes, undo: bool = False, redo: bool = False):
        self._buffer.append(TransactionDTO(update, datetime.now(), self.user_id, self.sid, undo, redo))

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    def drain(self) -> list[dict]:
        """Compress buffered transactions into history entries, one per run of plain / undo / redo updates."""
        buffered, self._buffer = self._buffer, []
        entries = []
        for (undo, redo), group in itertools.groupby(buffered, key=lambda dto: (dto.undo, dto.redo)):
            dtos = list(group)
            entries.append({
                "key": f"{self.user_id};{dtos[0].time.isoformat()}",
                "user_id": self.user_id,
                "sid": self.sid,
                "start": dtos[0].time.isoformat(),
                "end": dtos[-1].time.isoformat(),
                "undo": undo,
                "redo": redo,
                "transactions": len(dtos),
                "update": base64.b64encode(merge_updates(*(dto.update for dto in dtos))).decode(),
            })
        return entries

    def send_update(self, update: bytes):
        """Doc updates are batched per event-loop tick and merged into one emit."""
        if self._emit is None or not self.ready:
            return
        self._outbox.append(update)
        if not self._flushing:
            self._flushing = True
            self._spawn(self._flush_updates())

    async def _flush_updates(self):
        try:
            while self._outbox:
                updates, self._outbox = self._outbox, []
                await self._emit("update", updates[0] if len(updates) == 1 else merge_updates(*updates))
        finally:
            self._flushing = False

    def send(self, event: str, payload: Any):
        if self._emit is None or not self.ready:
            return
        self._spawn(self._emit(event, payload))

    def _spawn(self, coro):
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close()
            return
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task):
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("emit to %s failed", self.sid, exc_info=task.exception())

    def close(self):
        for task in self._tasks:
            task.cancel()
