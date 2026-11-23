from pathlib import Path
from pycrdt import Doc
from initialise_docs import Doc_template
from ..schema.InMemoryDoc import InMemoryDoc

def save_doc_to_path(doc: Doc, path: str):
    _path = Path(path)
    if _path.exists():
        document = create_doc_from_path(path)
        document.apply_update(doc.get_update(document.get_state()))
        _path.write_bytes(document.get_state())
        return
    _path.write_bytes(doc.get_state())


def delete_doc_from_path(path: str):
    Path(path).unlink(missing_ok=True)

def create_doc_from_path(path: str):
    document = Doc_template().apply_update(Path(path).read_bytes())
    return document 

def get_update(pulling_doc: InMemoryDoc, pushing_doc: InMemoryDoc): #other_doc is also a InMemoryDoc
        return pulling_doc.Doc.get_update(pushing_doc.Doc.get_state())