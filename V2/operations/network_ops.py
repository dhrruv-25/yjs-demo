from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pycrdt import TransactionEvent

from V2.operations.file_ops import get_update
from V2.schema.InMemoryDoc import RELAY

if TYPE_CHECKING:
    from V2.schema.RelayNetwork import RelayNetwork

log = logging.getLogger(__name__)


def publish(sender: str, network: RelayNetwork, update: bytes):
    for peer in network.nodes.keys():
        if peer != sender:
            network._relay_queue.put_nowait((peer, update))


def pull(puller: str, network: RelayNetwork):
    """Bring a joining doc up to date with the network. Every peer holds the same state, so one is enough."""
    doc = network.nodes[puller]
    peer = next((node for sid, node in network.nodes.items() if sid != puller), None)
    if peer is not None:
        doc.apply_update(get_update(doc, peer), origin=RELAY)


def _make_observer(sender: str, network: RelayNetwork):
    def on_update(transaction: TransactionEvent):
        doc = network.nodes.get(sender)
        if doc is None or doc.origin == RELAY:
            return
        publish(sender, network, transaction.update)
    return on_update


async def run(network: RelayNetwork):
    """Apply relayed updates in order. Runs as one background task per network."""
    while True:
        sid, update = await network._relay_queue.get()
        try:
            doc = network.nodes.get(sid)
            if doc is not None:
                doc.apply_update(update, origin=RELAY)
        except Exception:
            log.exception("relay to %s failed", sid)
        finally:
            network._relay_queue.task_done()
