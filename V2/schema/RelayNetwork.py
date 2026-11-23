from __future__ import annotations

import asyncio
import contextlib
import logging

from V2.operations.awareness_ops import awareness_client_ids
from V2.operations.file_ops import append_history
from V2.schema.InMemoryDoc import InMemoryDoc
from V2.schema.Session import Emitter, Session

log = logging.getLogger(__name__)


class RelayNetwork:
    """Every session editing one file, fanned out around that file's single InMemoryDoc."""

    def __init__(self, file_id: str, drain_seconds: float = 0) -> None:
        self.file_id = file_id
        self._flags = (False, False)
        self.doc = InMemoryDoc(file_id, on_update=self._fan_out)
        self.sessions: dict[str, Session] = {}
        self._owners: dict[int, str] = {}
        self._awareness_subscription = self.doc.awareness.observe(self._on_awareness)
        self._drain_seconds = drain_seconds
        self._drain_task: asyncio.Task | None = None

    # ---------- membership ----------

    def join(self, sid: str, user_id: str, emit: Emitter | None = None) -> Session:
        session = Session(sid, user_id, emit)
        self.sessions[sid] = session
        return session

    def leave(self, sid: str) -> Session | None:
        session = self.sessions.pop(sid, None)
        if session is None:
            return None
        append_history(self.file_id, session.drain())
        for client_id in session.client_ids:
            if self._owners.get(client_id) == sid:
                del self._owners[client_id]
        # Removing after the pop broadcasts the removal to everyone still here.
        self.doc.awareness.remove_awareness_states(list(session.client_ids), sid)
        # Forget the clocks too, or a reconnect that re-sends its last clock would be ignored as stale.
        for client_id in session.client_ids:
            self.doc.awareness.meta.pop(client_id, None)
        session.close()
        self.doc.save()
        return session

    def sync(self, sid: str, state_vector: bytes) -> dict:
        session = self.sessions[sid]
        session.ready = True
        update, state = self.doc.sync(state_vector)
        return {"update": update, "state": state, "awareness": self.doc.encode_awareness()}

    # ---------- document updates ----------

    def receive_update(self, sid: str, update: bytes, undo: bool = False, redo: bool = False):
        if sid not in self.sessions:
            return
        self._flags = (undo, redo)
        try:
            self.doc.apply_update(update, origin=sid)
        finally:
            self._flags = (False, False)

    def _fan_out(self, update: bytes, origin: str | None):
        # Only fires for updates that changed the doc, so no-op handshakes stay out of history.
        sender = self.sessions.get(origin)
        if sender is not None:
            sender.log_transaction(update, *self._flags)
        for sid, session in self.sessions.items():
            if sid != origin:
                session.send_update(update)

    # ---------- awareness (cursors) ----------

    def receive_awareness(self, sid: str, update: bytes) -> bool:
        """A session may only publish awareness for client ids it owns (or that its own user owned before a reconnect)."""
        session = self.sessions.get(sid)
        if session is None:
            return False
        try:
            client_ids = awareness_client_ids(update)
        except Exception:
            log.warning("malformed awareness update from %s", sid)
            return False
        for client_id in client_ids:
            owner = self.sessions.get(self._owners.get(client_id, sid))
            if owner is not None and owner is not session and owner.user_id != session.user_id:
                log.warning("sid %s tried to publish awareness for client %s owned by %s", sid, client_id, owner.sid)
                return False
        for client_id in client_ids:
            previous = self.sessions.get(self._owners.get(client_id, sid))
            if previous is not None and previous is not session:
                previous.client_ids.discard(client_id)
            self._owners[client_id] = sid
            session.client_ids.add(client_id)
        self.doc.awareness.apply_awareness_update(update, sid)
        return True

    def _on_awareness(self, topic: str, payload):
        if topic != "update":
            return
        changes, origin = payload
        client_ids = changes["added"] + changes["updated"] + changes["removed"]
        if not client_ids:
            return
        update = self.doc.awareness.encode_awareness_update(client_ids)
        for sid, session in self.sessions.items():
            if sid != origin:
                session.send("awareness", update)

    # ---------- history and persistence ----------

    def drain(self) -> int:
        drained = 0
        for session in list(self.sessions.values()):
            entries = session.drain()
            append_history(self.file_id, entries)
            drained += len(entries)
        return drained

    def save(self):
        self.drain()
        self.doc.save()

    async def _drain_loop(self):
        while True:
            await asyncio.sleep(self._drain_seconds)
            self.drain()

    def start(self):
        if self._drain_seconds > 0:
            self._drain_task = asyncio.create_task(self._drain_loop())

    async def stop(self):
        if self._drain_task is not None:
            self._drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._drain_task
            self._drain_task = None
        self.doc.awareness.unobserve(self._awareness_subscription)
        self.doc.close()
