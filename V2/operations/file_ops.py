from __future__ import annotations

import json
import uuid
from pathlib import Path

from pycrdt import Doc, Array, Text
from V2.operations import initialise_docs
from V2.operations.initialise_docs import Doc_template, Doc_constructor, doc_path, history_path


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


def doc_markdown(doc: Doc) -> str:
    return str(doc.get('md', type=Text))


def doc_lines(doc: Doc) -> list[str]:
    return doc_markdown(doc).split("\n")


def create_file() -> str:
    initialise_docs.docs_dir.mkdir(exist_ok=True)
    file_id = uuid.uuid4().hex
    doc_path(file_id).write_bytes(Doc_template().get_update())
    return file_id


def delete_file(file_id: str):
    delete_doc_from_path(doc_path(file_id))
    delete_doc_from_path(history_path(file_id))


def list_files() -> list[dict]:
    files = []
    for path in sorted(initialise_docs.docs_dir.glob("*.crdt"), key=lambda p: p.stat().st_mtime, reverse=True):
        doc = Doc_constructor(path.stem)
        title = "".join(doc.get('md_title', type=Array).to_py())
        if not title:
            title = next((line.lstrip("# ").strip() for line in doc_lines(doc) if line.strip()), "")
        files.append({"id": path.stem, "title": title or "Untitled", "modified": path.stat().st_mtime})
    return files


def append_history(file_id: str, entries: list[dict]):
    if not entries:
        return
    with history_path(file_id).open("a", encoding="utf-8") as history:
        for entry in entries:
            history.write(json.dumps(entry) + "\n")


def read_history(file_id: str) -> list[dict]:
    path = history_path(file_id)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
