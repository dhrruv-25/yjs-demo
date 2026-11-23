from __future__ import annotations

import asyncio
import base64
import itertools
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable

from pycrdt import Array, Doc, Map, MapEvent, StickyIndex, TransactionEvent, merge_updates

from V2.operations.file_ops import get_update, save_doc_to_path, doc_lines
from V2.operations.initialise_docs import Doc_constructor, doc_path
from V2.operations.render import render_doc

log = logging.getLogger(__name__)

CLIENT = "client"
RELAY = "relay"
SERVER = "server"

Emitter = Callable[[bytes], Awaitable[None]]


@dataclass
class TransactionDTO:
    update: bytes
    time: datetime
    user_id: str
    sid: str
    undo: bool
    redo: bool = False


class InMemoryDoc:
    def __init__(self, sid : str, user_id : str, doc_id : str | None = None, emit: Emitter | None = None) -> None:
        self.sid = sid
        self.user_id = user_id
        self.file_id = uuid.uuid4().hex if doc_id is None else doc_id
        self.Doc: Doc = Doc_constructor(doc_id=self.file_id)
        self._buffer: list[TransactionDTO] = []
        self._origin: str | None = None
        self._undo = False
        self._redo = False
        self._emit = emit
        # Updates are only pushed to the client after its sync handshake; earlier ones arrive in the handshake diff.
        self.ready = False
        self._outbox: list[bytes] = []
        self._flush_task: asyncio.Task | None = None
        # pycrdt can only unobserve through the same wrapper object that observed.
        cursors = self.cursors
        self._subscriptions = [
            (self.Doc, self.Doc.observe(self._on_transaction)),
            (cursors, cursors.observe(self._guard_cursors)),
        ]

    @property
    def lines(self) -> Array:
        return self.Doc.get('md', type=Array)

    @property
    def cursors(self) -> Map:
        return self.Doc.get('cursor', type=Map)

    @property
    def history(self) -> Map:
        return self.Doc.get('history', type=Map)

    @property
    def origin(self) -> str | None:
        return self._origin

    def apply_update(self, update: bytes, origin: str = RELAY, undo: bool = False, redo: bool = False):
        self._origin, self._undo, self._redo = origin, undo, redo
        try:
            self.Doc.apply_update(update)
        finally:
            self._origin, self._undo, self._redo = None, False, False

    def transact(self, change: Callable[[Doc], None]):
        """Server-originated change: relayed to peers and emitted to this doc's own client."""
        self._origin = SERVER
        try:
            with self.Doc.transaction():
                change(self.Doc)
        finally:
            self._origin = None

    def get_state(self):
        return self.Doc.get_state()

    def get_update(self, other_doc: InMemoryDoc):
        return get_update(self, other_doc)

    def sync(self, state_vector: bytes) -> tuple[bytes, bytes]:
        """Answer a client's state vector with (what it is missing, our state vector)."""
        self.ready = True
        return self.Doc.get_update(state_vector), self.Doc.get_state()

    def observe(self, callback: Callable[[TransactionEvent], None]):
        return self.Doc.observe(callback)

    def _on_transaction(self, event: TransactionEvent):
        if self._origin == CLIENT:
            self.log_transaction(event.update, self._undo, self._redo)
        else:
            self._queue_emit(event.update)

    def _queue_emit(self, update: bytes):
        if self._emit is None or not self.ready:
            return
        self._outbox.append(update)
        if self._flush_task is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                self._outbox.clear()
                return
            self._flush_task = loop.create_task(self._flush_outbox())

    async def _flush_outbox(self):
        try:
            while self._outbox:
                updates, self._outbox = self._outbox, []
                await self._emit(updates[0] if len(updates) == 1 else merge_updates(*updates))
        except Exception:
            log.exception("emit to %s failed", self.sid)
        finally:
            self._flush_task = None

    def _guard_cursors(self, event: MapEvent):
        """A client may only write its own sid's cursor; anything else is reverted."""
        if self._origin != CLIENT:
            return
        foreign = {key: change for key, change in event.keys.items() if key != self.sid}
        if not foreign:
            return
        log.warning("sid %s wrote foreign cursors %s; reverting", self.sid, list(foreign))
        try:
            asyncio.get_running_loop().call_soon(self._revert_cursors, foreign)
        except RuntimeError:
            log.error("no event loop to revert foreign cursor writes from %s", self.sid)

    def _revert_cursors(self, changes: dict):
        def revert(doc: Doc):
            cursors = doc.get('cursor', type=Map)
            for key, change in changes.items():
                if change["action"] == "add":
                    cursors.pop(key, None)
                else:
                    cursors[key] = change["oldValue"]
        self.transact(revert)

    def log_transaction(self, update: bytes, undo: bool, redo: bool = False):
        DTO: TransactionDTO = TransactionDTO(
            update,
            datetime.now(),
            self.user_id,
            self.sid,
            undo,
            redo)
        self._buffer.append(DTO)

    def drain(self) -> int:
        """Compress buffered transactions into history entries, one per run of plain / undo / redo updates."""
        if not self._buffer:
            return 0
        buffered, self._buffer = self._buffer, []
        entries = {}
        for (undo, redo), group in itertools.groupby(buffered, key=lambda dto: (dto.undo, dto.redo)):
            dtos = list(group)
            entries[f"{self.user_id};{dtos[0].time.isoformat()}"] = {
                "user_id": self.user_id,
                "sid": self.sid,
                "start": dtos[0].time.isoformat(),
                "end": dtos[-1].time.isoformat(),
                "undo": undo,
                "redo": redo,
                "transactions": len(dtos),
                "update": base64.b64encode(merge_updates(*(dto.update for dto in dtos))).decode(),
            }

        def write(doc: Doc):
            history = doc.get('history', type=Map)
            for key, entry in entries.items():
                history[key] = entry
        self.transact(write)
        return len(entries)

    def remove_cursor(self, sid: str):
        if sid in self.cursors:
            self.transact(lambda doc: doc.get('cursor', type=Map).pop(sid))

    def clear_cursors(self):
        if len(self.cursors):
            self.transact(lambda doc: doc.get('cursor', type=Map).clear())

    def cursor_positions(self) -> dict[str, dict]:
        """Resolve every sid's sticky (line, col) pair to plain indexes."""
        lines = self.lines
        positions = {}
        for sid, cursor in self.cursors.to_py().items():
            try:
                line = StickyIndex.decode(bytes(cursor["line"]), lines).get_index()
                line = min(line, len(lines) - 1)
                col = StickyIndex.decode(bytes(cursor["col"]), lines[line]).get_index()
            except Exception:
                continue
            positions[sid] = {"user_id": cursor.get("user_id"), "line": line, "col": col}
        return positions

    def read(self) -> list[str]:
        return doc_lines(self.Doc)

    def render(self) -> str:
        return render_doc(self.Doc)

    def save(self):
        save_doc_to_path(self.Doc, doc_path(self.file_id))

    def flush(self):
        self.drain()
        self.save()

    def close(self):
        for target, subscription in self._subscriptions:
            target.unobserve(subscription)
        self._subscriptions.clear()
        if self._flush_task is not None:
            self._flush_task.cancel()
