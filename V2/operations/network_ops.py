from ..schema.InMemoryDoc import InMemoryDoc, TransactionDTO
from ..schema.RelayNetwork import RelayNetwork
from pycrdt import TransactionEvent
from file_ops import get_update

def publish(sender: str, network: RelayNetwork, transaction: TransactionEvent):
    for peer in network.nodes.keys():
        if peer != sender:
            network._relay_queue.put_nowait((peer, transaction.update))

def pull(puller: str, network: RelayNetwork):
    for peer in network.nodes.keys():
        if peer != puller:
            network._relay_queue.put_nowait((puller, get_update(network.nodes[puller], network.nodes[peer])))

def _make_observer(sender: str, network: RelayNetwork):
    def on_update(transaction: TransactionEvent):
        if sender in network._applying or not transaction.update:
            return
        network.publish(sender, network, transaction)
    return on_update

async def run(network: RelayNetwork):
    """Apply incoming updates. Runs as a background task per peer."""
    while True:
        sid, update = await network._relay_queue.get()
        doc = network.nodes[sid]
        if doc is None:
            continue
        network._applying.add(sid)
        try:
            # Observers fire synchronously here, so the guard covers them
            doc.apply_update(update)
        finally:
            network._applying.discard(sid)

