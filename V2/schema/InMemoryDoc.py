from operations.helper import create_doc_from_path, save_doc_to_path
from pycrdt import Doc, TransactionEvent
from datetime import datetime
import uuid 
from dataclasses import dataclass
from operations.initialise_docs import Doc_constructor, docs_dir
from typing import Dict, Callable
from ..operations.file_ops import get_update


class InMemoryDoc:
    def __init__(self, sid : int, user_id : str, doc_id : str | None = None) -> None:
        self.sid = sid
        self.user_id = user_id
        self.file_id = uuid.uuid1() if doc_id is None else doc_id
        self.Doc: Doc = Doc_constructor(doc_id=doc_id)
        self._buffer: Dict[datetime, TransactionDTO] = {}
        self._subscriptions: list[str] = []
    
    def apply_update(self, update: bytes):
        self.Doc.apply_update(update)
    
    def get_state(self):
        return self.Doc.get_state()
    
    def get_update(self, other_doc): #other_doc is also a InMemoryDoc
        return get_update(self, other_doc)
    
    def observe(self, callback: Callable):
        return self.Doc.observe(callback)
    
    def log_transaction(self, transaction: TransactionEvent, undo: bool):
        DTO: TransactionDTO = TransactionDTO(
            transaction, 
            datetime.now(), 
            self.user_id, 
            self.sid,
            undo)
        self._buffer[DTO.time] = DTO

    def read(self):
        return self.Doc['md'].to_py()
    
    def save(self):
        path = docs_dir / f'{self.file_id}.crdt'
        save_doc_to_path(self.Doc, path)

    def flush(self):
        self.save()
        ## must also handle buffer


@dataclass
class TransactionDTO:
    transaction: TransactionEvent 
    time: datetime
    user_id: uuid
    sid: str
    undo: bool 