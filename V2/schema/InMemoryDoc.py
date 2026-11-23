from __future__ import annotations

from typing import Callable

from pycrdt import Awareness, Doc, StickyIndex, Text, TransactionEvent

from V2.operations.awareness_ops import offset_to_line_col
from V2.operations.file_ops import doc_markdown, save_doc_to_path
from V2.operations.initialise_docs import Doc_constructor, doc_path
from V2.operations.render import render_doc

UpdateListener = Callable[[bytes, str | None], None]


class InMemoryDoc:
    """The one server replica of a file. Sessions read and write through it; cursors live in its Awareness."""

    def __init__(self, file_id: str, on_update: UpdateListener | None = None) -> None:
        self.file_id = file_id
        self.Doc: Doc = Doc_constructor(doc_id=file_id)
        self.awareness = Awareness(self.Doc)
        # The server has no cursor of its own, so it must not appear as a peer.
        self.awareness.set_local_state(None)
        self._origin: str | None = None
        self._on_update = on_update
        self._subscription = self.Doc.observe(self._on_transaction)

    @property
    def text(self) -> Text:
        return self.Doc.get('md', type=Text)

    def apply_update(self, update: bytes, origin: str | None = None):
        self._origin = origin
        try:
            self.Doc.apply_update(update)
        finally:
            self._origin = None

    def _on_transaction(self, event: TransactionEvent):
        if self._on_update is not None:
            self._on_update(event.update, self._origin)

    def sync(self, state_vector: bytes) -> tuple[bytes, bytes]:
        """Answer a client's state vector with (what it is missing, our state vector)."""
        return self.Doc.get_update(state_vector), self.Doc.get_state()

    def get_state(self) -> bytes:
        return self.Doc.get_state()

    def encode_awareness(self) -> bytes | None:
        client_ids = list(self.awareness.states)
        return self.awareness.encode_awareness_update(client_ids) if client_ids else None

    def cursor_positions(self) -> dict[int, dict]:
        """Resolve every awareness cursor (Yjs relative positions) with pycrdt StickyIndexes."""
        text = self.text
        content = str(text)
        positions = {}
        for client_id, state in self.awareness.states.items():
            cursor = (state or {}).get("cursor")
            if not cursor:
                continue
            try:
                anchor = StickyIndex.from_json(cursor["anchor"], text).get_index()
                head = StickyIndex.from_json(cursor["head"], text).get_index()
            except Exception:
                continue
            line, col = offset_to_line_col(content, head)
            positions[client_id] = {"user": state.get("user"), "anchor": anchor, "head": head, "line": line, "col": col}
        return positions

    def read(self) -> str:
        return doc_markdown(self.Doc)

    def render(self) -> str:
        return render_doc(self.Doc)

    def save(self):
        save_doc_to_path(self.Doc, doc_path(self.file_id))

    def close(self):
        self.Doc.unobserve(self._subscription)
