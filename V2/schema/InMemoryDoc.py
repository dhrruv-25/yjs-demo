from pycrdt import Doc, TransactionEvent
from datetime import datetime
import uuid 
from dataclasses import dataclass
from operations.initialise_docs import Doc_constructor
from typing import Dict, Callable

class InMemoryDoc:
    def __init__(self, sid : int, user_id : str, doc_id : str) -> None:
        self.sid = sid
        self.user_id = user_id
        self.file_id = doc_id
        self.Doc: Doc = Doc_constructor(client_id=sid, doc_id=doc_id)
        self._buffer: Dict[datetime, TransactionDTO] = {}
        self._subscriptions: list[str] = []
    
    def apply_update(self, update: bytes):
        self.Doc.apply_update(update)
    
    def get_state(self):
        return self.Doc.get_state()
    
    def observe(self, callback: Callable):
        return self.Doc.observe(callback)
    
    async def log_transaction(self, transaction: TransactionEvent, undo: bool):
        DTO: TransactionDTO = TransactionDTO(
            transaction, 
            datetime.now(), 
            self.user_id, 
            self.sid,
            undo)
        self._buffer[DTO.time] = DTO

    async def read(self):
        return self.Doc['md'].to_py()

@dataclass
class TransactionDTO:
    transaction: TransactionEvent 
    time: datetime
    user_id: uuid
    sid: str
    undo: bool 