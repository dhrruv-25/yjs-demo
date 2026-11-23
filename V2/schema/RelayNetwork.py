from queue import Empty
from typing import Dict, Set
import uuid
from pycrdt import TransactionEvent
from V2.schema import InMemoryDoc
import asyncio
from ..operations.network_ops import publish, pull, _make_observer, run


class RelayNetwork:
    def __init__(self, MemDoc: InMemoryDoc) -> None:
        self.file_id = MemDoc.file_id
        self.nodes = Dict[MemDoc.sid, MemDoc] = {}
        self._relay_queue: asyncio.Queue[tuple[MemDoc.id, bytes]] = asyncio.Queue()
        self._applying: Set = {}
    
    def join_network(self, MemDoc: InMemoryDoc):
        self.nodes[MemDoc.sid] = MemDoc 
        pull(MemDoc.sid, self)
        MemDoc.observe(_make_observer(MemDoc.sid, self))
    
    def leave_network(self, MemDoc_id: str):
        doc = self.nodes[MemDoc_id]
        doc.flush()
        self.nodes.pop(MemDoc_id)
    
    def publish(self, sender:str, transaction: TransactionEvent):
        publish(sender, self, TransactionEvent)
    
    async def relay(self):
        await run(self)

