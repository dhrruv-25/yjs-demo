from pycrdt import Doc, Array, Map
import os
from pathlib import Path


docs_dir = Path(__file__).resolve().parents[2] / "documents"

def doc_id_exists(doc_id: str):
    return f'{doc_id}.crdt' in os.listdir(docs_dir)

def Doc_template():

    Doc_template = Doc()
    Doc_template['md'] = Array()
    Doc_template['history'] = Map()

    return Doc_template

def Doc_constructor(doc_id : str = None):

    doc = Doc_template()

    if doc_id_exists(doc_id):
        copy_path = docs_dir.joinpath(f'{doc_id}.crdt')
        copy_bytes = copy_path.read_bytes()
        doc.apply_update(copy_bytes)
    
    return doc 