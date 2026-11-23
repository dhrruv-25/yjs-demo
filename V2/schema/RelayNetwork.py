from typing import Dict 
import uuid
from V2.schema import InMemoryDoc
from asyncio import afor


class RelayNetwork:
    def __init__(self, MemDoc: InMemoryDoc) -> None:
        self.file_id = MemDoc.file_id
        self.nodes = Dict[MemDoc.sid, MemDoc]
    
    async def join_network(self, MemDoc: InMemoryDoc):
        self.nodes[MemDoc.sid] = MemDoc 
    
    async def relay_sync(self):
        nodes = self.nodes.keys()

        async for node1 in nodes:
            async for node2 in nodes:
                if not node1 == node2:
                    
