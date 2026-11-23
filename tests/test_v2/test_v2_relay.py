import base64

import pytest
from pycrdt import Array, Doc

from V2.operations.file_ops import create_file, doc_markdown, read_history
from V2.operations.initialise_docs import Doc_constructor, Doc_template, doc_path

pytestmark = pytest.mark.anyio


def item(doc: Doc, index: int) -> dict:
    """Y.relativePositionToJSON for a position inside the text."""
    return doc["md"].sticky_index(index).to_json()


END = {"tname": "md", "assoc": 0}  # Y.relativePositionToJSON for the end of the root text


async def test_two_clients_converge_on_one_server_doc(hub):
    file_id = create_file()
    a, b = hub.client("alice"), hub.client("bob")
    a.connect(file_id)
    b.connect(file_id)

    a.insert(0, "hello")
    await hub.settle()
    b.insert(5, "\nworld")
    await hub.settle()

    network = hub.registry.network(file_id)
    assert a.text == b.text == network.doc.read() == "hello\nworld"
    assert set(network.sessions) == {a.sid, b.sid}


async def test_updates_are_not_echoed_to_their_sender(hub):
    file_id = create_file()
    a, b = hub.client("alice"), hub.client("bob")
    a.connect(file_id)
    b.connect(file_id)
    a.received.clear()

    a.insert(0, "x")
    await hub.settle()

    assert [event for event, _ in a.received if event == "update"] == []
    assert b.text == "x"


async def test_concurrent_edits_converge(hub):
    file_id = create_file()
    a, b = hub.client("alice"), hub.client("bob")
    a.connect(file_id)
    b.connect(file_id)
    a.insert(0, "abc")
    await hub.settle()

    a.edit(lambda md: md.insert(0, "X"))
    b.edit(lambda md: md.insert(3, "Y"))
    await hub.settle()

    assert a.text == b.text == "XabcY"


async def test_late_joiner_receives_text_and_cursors_in_handshake(hub):
    file_id = create_file()
    a = hub.client("alice")
    a.connect(file_id)
    a.insert(0, "# Title\nbody")
    a.set_cursor(item(a.doc, 3))
    await hub.settle()

    b = hub.client("bob")
    b.connect(file_id)
    assert b.text == "# Title\nbody"
    assert [state["user"]["id"] for state in b.peers.values()] == ["alice"]


async def test_offline_edits_merge_on_reconnect(hub):
    file_id = create_file()
    a, b = hub.client("alice"), hub.client("bob")
    a.connect(file_id)
    b.connect(file_id)
    a.insert(0, "shared")
    await hub.settle()

    await a.disconnect()
    a.insert(6, " +offline")
    b.insert(0, "online+ ")
    await hub.settle()
    assert "offline" not in b.text

    a.connect(file_id)
    await hub.settle()
    assert a.text == b.text == "online+ shared +offline"


async def test_last_leave_saves_only_the_text(hub):
    file_id = create_file()
    a = hub.client("alice")
    a.connect(file_id)
    a.insert(0, "persist me")
    a.set_cursor(END)
    await a.disconnect()

    assert hub.registry.network(file_id) is None
    saved = Doc()
    saved.apply_update(doc_path(file_id).read_bytes())
    assert doc_markdown(Doc_constructor(file_id)) == "persist me"
    assert set(saved.keys()) == {"md"}


async def test_cursors_are_relayed_and_resolved_with_sticky_indexes(hub):
    file_id = create_file()
    a, b = hub.client("alice"), hub.client("bob")
    a.connect(file_id)
    b.connect(file_id)
    a.insert(0, "hello\nworld")
    await hub.settle()

    assert a.set_cursor(item(a.doc, 2), END)
    b.insert(0, ">> ")
    await hub.settle()

    assert b.peers[a.doc.client_id]["cursor"]["head"] == END
    position = hub.registry.network(file_id).doc.cursor_positions()[a.doc.client_id]
    assert position == {"user": {"id": "alice", "name": "alice", "color": "#7aa2f7"},
                        "anchor": 5, "head": 14, "line": 1, "col": 5}


async def test_cursor_removed_from_peers_on_disconnect(hub):
    file_id = create_file()
    a, b = hub.client("alice"), hub.client("bob")
    a.connect(file_id)
    b.connect(file_id)
    a.set_cursor(END)
    await hub.settle()
    assert a.doc.client_id in b.peers

    await a.disconnect()
    await hub.settle()
    assert a.doc.client_id not in b.peers
    assert a.doc.client_id not in hub.registry.network(file_id).doc.awareness.states


async def test_cursor_reappears_after_reconnect_without_moving(hub):
    file_id = create_file()
    a, b = hub.client("alice"), hub.client("bob")
    a.connect(file_id)
    b.connect(file_id)
    a.set_cursor(END)
    await hub.settle()

    await a.disconnect()
    await hub.settle()
    assert a.doc.client_id not in b.peers

    a.connect(file_id)
    await hub.settle()
    assert b.peers[a.doc.client_id]["cursor"]["head"] == END


async def test_session_cannot_publish_another_users_cursor(hub):
    file_id = create_file()
    a, b = hub.client("alice"), hub.client("bob")
    a.connect(file_id)
    b.connect(file_id)

    b.awareness.apply_awareness_update(
        a.awareness.encode_awareness_update([a.doc.client_id]), "forged")
    b.awareness._meta[a.doc.client_id]["clock"] += 5
    assert b.publish_awareness([a.doc.client_id]) is False


async def test_same_user_can_reclaim_its_cursor_after_reconnect(hub):
    file_id = create_file()
    first = hub.client("alice")
    first.connect(file_id)
    second = hub.client("alice")
    second.doc = first.doc
    second.awareness = first.awareness
    second.connect(file_id)
    assert second.set_cursor(END) is True


async def test_history_lives_outside_the_doc(hub):
    file_id = create_file()
    a, b = hub.client("alice"), hub.client("bob")
    a.connect(file_id)
    b.connect(file_id)
    a.insert(0, "a")
    a.insert(1, "b")
    a.edit(lambda md: md.__delitem__(slice(1, 2)), undo=True)
    a.edit(lambda md: md.insert(1, "b"), redo=True)
    a.insert(2, "c")

    assert hub.registry.network(file_id).drain() == 4
    entries = read_history(file_id)
    assert [(e["undo"], e["redo"], e["transactions"]) for e in entries] == [
        (False, False, 2), (True, False, 1), (False, True, 1), (False, False, 1)]
    assert all(e["sid"] == a.sid and e["user_id"] == "alice" for e in entries)
    assert "history" not in b.doc.keys()

    replay = Doc_template()
    for entry in entries:
        replay.apply_update(base64.b64decode(entry["update"]))
    assert str(replay["md"]) == "abc"


async def test_noop_updates_are_not_logged(hub):
    file_id = create_file()
    a = hub.client("alice")
    a.connect(file_id)
    a.insert(0, "abc")
    a.edit(lambda md: md.__delitem__(slice(0, 1)))
    session = hub.registry.network(file_id).sessions[a.sid]
    assert session.buffered == 2

    a.network.receive_update(a.sid, a.doc.get_update(a.network.doc.get_state()))
    assert session.buffered == 2


async def test_leave_flushes_the_buffer_to_history(hub):
    file_id = create_file()
    a = hub.client("alice")
    a.connect(file_id)
    a.insert(0, "x")
    await a.disconnect()
    assert [e["transactions"] for e in read_history(file_id)] == [1]


def test_v1_documents_are_migrated_to_text(v2_docs):
    v1 = Doc()
    v1["markdown_arr"] = Array(list("# Heading\n\nbody"))
    v1["md_title"] = Array(list("Title"))
    doc_path("legacy").write_bytes(v1.get_update())

    assert doc_markdown(Doc_constructor("legacy")) == "# Heading\n\nbody"
    assert doc_markdown(Doc_constructor("legacy")) == "# Heading\n\nbody"
