from pycrdt import Doc, Array, Map, Text
from pathlib import Path
import os
import re


docs_dir = Path(os.environ.get("V2_DOCS_DIR") or Path(__file__).resolve().parents[2] / "documents")

_DOC_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def valid_doc_id(doc_id: str | None) -> bool:
    return bool(doc_id) and bool(_DOC_ID.match(doc_id))


def doc_path(doc_id: str) -> Path:
    if not valid_doc_id(doc_id):
        raise ValueError(f"invalid doc id: {doc_id!r}")
    return docs_dir / f"{doc_id}.crdt"


def doc_id_exists(doc_id: str | None) -> bool:
    return valid_doc_id(doc_id) and doc_path(doc_id).exists()


def Doc_template():

    Doc_template = Doc()
    Doc_template['md'] = Array()
    Doc_template['history'] = Map()
    Doc_template['cursor'] = Map()

    return Doc_template


def _migrate_v1(doc: Doc) -> bool:
    """V1 stored the markdown as an Array of characters under 'markdown_arr'."""
    md = doc.get('md', type=Array)
    if len(md):
        return False
    chars = doc.get('markdown_arr', type=Array).to_py()
    if not chars:
        return False
    with doc.transaction():
        for line in "".join(chars).split("\n"):
            md.append(Text(line))
    return True


def Doc_constructor(doc_id : str | None = None):

    doc = Doc_template()

    if doc_id_exists(doc_id):
        copy_path = doc_path(doc_id)
        doc.apply_update(copy_path.read_bytes())
        if _migrate_v1(doc):
            copy_path.write_bytes(doc.get_update())

    return doc
