from pathlib import Path

import pycrdt as cr
import pytest
from bson import ObjectId

import app as app_module
from controller import markdown_session


@pytest.fixture
def docs_dir(tmp_path, monkeypatch):
    folder = tmp_path / "documents"
    folder.mkdir()
    monkeypatch.setattr(markdown_session, "DOCS_DIR", folder)
    return folder


@pytest.fixture
def app_docs(tmp_path, monkeypatch):
    """Point the app and sessions at a temp documents folder without touching real files."""
    root = Path(app_module.__file__).resolve().parent
    folder = tmp_path / "documents"
    folder.mkdir()
    (tmp_path / "static").symlink_to(root / "static")
    monkeypatch.setattr(markdown_session, "DOCS_DIR", folder)
    monkeypatch.setattr(app_module, "ROOT", tmp_path)
    return folder


def write_doc(folder: Path, text: str = "", title: str = "", file_id: ObjectId | None = None) -> ObjectId:
    file_id = file_id or ObjectId()
    doc = cr.Doc()
    doc["markdown_arr"] = cr.Array(list(text))
    doc["md_title"] = cr.Array(list(title))
    (folder / f"{file_id}.crdt").write_bytes(doc.get_update())
    return file_id
