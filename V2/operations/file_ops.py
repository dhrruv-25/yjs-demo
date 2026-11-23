from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
import uuid

from pycrdt import Doc, Array, Text
from V2.operations import initialise_docs
from V2.operations.initialise_docs import Doc_template, Doc_constructor, doc_path

if TYPE_CHECKING:
    from V2.schema.InMemoryDoc import InMemoryDoc


def save_doc_to_path(doc: Doc, path: str | Path):
    _path = Path(path)
    if _path.exists():
        document = create_doc_from_path(_path)
        document.apply_update(doc.get_update(document.get_state()))
        _path.write_bytes(document.get_update())
        return
    _path.write_bytes(doc.get_update())


def delete_doc_from_path(path: str | Path):
    Path(path).unlink(missing_ok=True)


def create_doc_from_path(path: str | Path) -> Doc:
    document = Doc_template()
    document.apply_update(Path(path).read_bytes())
    return document


def get_update(pulling_doc: InMemoryDoc, pushing_doc: InMemoryDoc) -> bytes:
    """The update pulling_doc needs in order to catch up with pushing_doc."""
    return pushing_doc.Doc.get_update(pulling_doc.Doc.get_state())


def doc_lines(doc: Doc) -> list[str]:
    return [str(line) for line in doc.get('md', type=Array)]


def doc_markdown(doc: Doc) -> str:
    return "\n".join(doc_lines(doc))


def create_file() -> str:
    initialise_docs.docs_dir.mkdir(exist_ok=True)
    file_id = uuid.uuid4().hex
    doc = Doc_template()
    doc.get('md', type=Array).append(Text(""))
    doc_path(file_id).write_bytes(doc.get_update())
    return file_id


def list_files() -> list[dict]:
    files = []
    for path in sorted(initialise_docs.docs_dir.glob("*.crdt"), key=lambda p: p.stat().st_mtime, reverse=True):
        doc = Doc_constructor(path.stem)
        title = "".join(doc.get('md_title', type=Array).to_py())
        if not title:
            title = next((line.lstrip("# ").strip() for line in doc_lines(doc) if line.strip()), "")
        files.append({"id": path.stem, "title": title or "Untitled", "modified": path.stat().st_mtime})
    return files
