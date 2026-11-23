import base64

import pytest
from pycrdt import Array, Doc, Map, Text

from V2.operations.file_ops import create_file, doc_lines
from V2.operations.initialise_docs import Doc_constructor, doc_path

pytestmark = pytest.mark.anyio


def _varuint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte, n = n & 0x7F, n >> 7
        out.append(byte | 0x80 if n else byte)
        if not n:
            return bytes(out)


def yjs_end_of_line(doc: Doc, line: int) -> bytes:
    """What Y.encodeRelativePosition produces for a caret at the end of a line (pycrdt panics creating one)."""
    item = doc["md"].sticky_index(line).to_json()["item"]
    return b"\x02" + _varuint(item["client"]) + _varuint(item["clock"]) + b"\x00"


async def test_two_clients_converge(net):
    file_id = create_file()
    a, b = net.client("alice"), net.client("bob")
    a.connect(file_id)
    b.connect(file_id)

    a.insert(0, 0, "hello")
    b.append_line("world")
    await net.settle()

    assert a.lines == b.lines == ["hello", "world"]
    for node in net.registry.network(file_id).nodes.values():
        assert node.read() == ["hello", "world"]


async def test_concurrent_edits_on_one_line_converge(net):
    file_id = create_file()
    a, b = net.client("alice"), net.client("bob")
    a.connect(file_id)
    b.connect(file_id)
    a.insert(0, 0, "abc")
    await net.settle()

    a.insert(0, 0, "X")
    b.insert(0, 3, "Y")
    await net.settle()

    assert a.lines == b.lines == ["XabcY"]


async def test_late_joiner_receives_state_in_handshake(net):
    file_id = create_file()
    a = net.client("alice")
    a.connect(file_id)
    a.insert(0, 0, "# Title")
    a.append_line("body")
    await net.settle()

    b = net.client("bob")
    b.connect(file_id)
    assert b.lines == ["# Title", "body"]


async def test_offline_edits_merge_on_reconnect(net):
    file_id = create_file()
    a, b = net.client("alice"), net.client("bob")
    a.connect(file_id)
    b.connect(file_id)
    a.insert(0, 0, "shared")
    await net.settle()

    await a.disconnect()
    a.append_line("written offline")
    b.append_line("written online")
    await net.settle()
    assert "written offline" not in b.lines

    a.connect(file_id)
    await net.settle()

    assert a.lines == b.lines
    assert set(a.lines) == {"shared", "written offline", "written online"}


async def test_last_leave_saves_and_closes_network(net):
    file_id = create_file()
    a = net.client("alice")
    a.connect(file_id)
    a.insert(0, 0, "persist me")
    await net.settle()
    await a.disconnect()

    assert net.registry.network(file_id) is None
    assert doc_lines(Doc_constructor(file_id)) == ["persist me"]


async def test_client_cannot_write_another_sids_cursor(net):
    file_id = create_file()
    a, b = net.client("alice"), net.client("bob")
    a.connect(file_id)
    b.connect(file_id)
    a.insert(0, 0, "hello")
    await net.settle()

    def own_cursor(doc: Doc):
        line = doc["md"].sticky_index(0)
        col = doc["md"][0].sticky_index(2)
        doc["cursor"][a.sid] = {"user_id": "alice", "line": line.encode(), "col": col.encode()}

    a.edit(own_cursor)
    a.edit(lambda doc: doc["cursor"].__setitem__(b.sid, {"user_id": "mallory"}))
    await net.settle()

    for client in (a, b):
        cursors = client.doc.get("cursor", type=Map).to_py()
        assert set(cursors) == {a.sid}
    positions = net.registry.network(file_id).any_node().cursor_positions()
    assert positions[a.sid] == {"user_id": "alice", "line": 0, "col": 2}


async def test_cursor_follows_remote_edits_and_is_removed_on_disconnect(net):
    file_id = create_file()
    a, b = net.client("alice"), net.client("bob")
    a.connect(file_id)
    b.connect(file_id)
    a.insert(0, 0, "hello")
    await net.settle()

    a.edit(lambda doc: doc["cursor"].__setitem__(a.sid, {
        "user_id": "alice",
        "line": doc["md"].sticky_index(0).encode(),
        "col": yjs_end_of_line(doc, 0),
    }))
    b.insert(0, 0, ">> ")
    await net.settle()
    node = net.registry.docs[b.sid]
    assert node.cursor_positions()[a.sid]["col"] == 8

    a_sid = a.sid
    await a.disconnect()
    await net.settle()
    assert a_sid not in b.doc.get("cursor", type=Map).to_py()


async def test_buffer_drains_into_history_grouped_by_undo_flag(net):
    file_id = create_file()
    a, b = net.client("alice"), net.client("bob")
    a.connect(file_id)
    b.connect(file_id)
    a.insert(0, 0, "a")
    a.insert(0, 1, "b")
    a.edit(lambda doc: doc["md"][0].__delitem__(slice(1, 2)), undo=True)
    a.insert(0, 1, "c")

    assert len(a.node._buffer) == 4
    assert a.node.drain() == 3
    assert a.node._buffer == []
    await net.settle()

    history = b.doc.get("history", type=Map).to_py()
    entries = sorted(history.values(), key=lambda entry: entry["start"])
    assert [(e["undo"], e["transactions"]) for e in entries] == [(False, 2), (True, 1), (False, 1)]
    assert all(e["sid"] == a.sid and e["user_id"] == "alice" for e in entries)

    replay = Doc()
    replay["md"] = Array()
    replay.apply_update(Doc_constructor(file_id).get_update())
    for entry in entries:
        replay.apply_update(base64.b64decode(entry["update"]))
    assert [str(line) for line in replay["md"]] == ["ac"]


async def test_history_drain_does_not_reenter_the_buffer(net):
    file_id = create_file()
    a = net.client("alice")
    a.connect(file_id)
    a.insert(0, 0, "x")
    a.node.drain()
    assert a.node._buffer == []


def test_v1_documents_are_migrated_to_lines(v2_docs):
    v1 = Doc()
    v1["markdown_arr"] = Array(list("# Heading\n\nbody"))
    v1["md_title"] = Array(list("Title"))
    doc_path("legacy").write_bytes(v1.get_update())

    assert doc_lines(Doc_constructor("legacy")) == ["# Heading", "", "body"]
    assert doc_lines(Doc_constructor("legacy")) == ["# Heading", "", "body"]
