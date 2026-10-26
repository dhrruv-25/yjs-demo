import asyncio

import pytest

from controller.markdown_session import MarkdownSession
from operations.helper import get_doc_copy_from_path, get_title_from_path, parse_doc_to_md_str
from schemas.exceptions import (
    DeleteDocumentError,
    DuplicateMemberError,
    MemberNotFoundError,
    SaveDocumentError,
)
from tests.conftest import write_doc


def test_new_member_starts_at_end_of_document(docs_dir):
    file_id = write_doc(docs_dir, text="hello")
    session = MarkdownSession(file_id)

    session.add_member("alice")
    session.add_member("bob")

    assert session.indexes == {"alice": 5, "bob": 5}
    assert session.title_index == {"alice": None, "bob": None}
    assert set(session.get_members()) == {"alice", "bob"}
    assert session.get_title() == ""


def test_empty_document_member_starts_at_zero(docs_dir):
    session = MarkdownSession(write_doc(docs_dir, text=""))

    session.add_member("alice")

    assert session.indexes["alice"] == 0


def test_add_member_rejects_duplicates_and_drop_requires_a_member(docs_dir):
    session = MarkdownSession(write_doc(docs_dir, text="ab"))
    session.add_member("alice")

    with pytest.raises(DuplicateMemberError):
        session.add_member("alice")
    with pytest.raises(MemberNotFoundError):
        session.drop_member("missing")

    session.drop_member("alice")

    assert "alice" not in session.indexes
    assert "alice" not in session.title_index


def test_title_roundtrip_is_in_memory_until_save(docs_dir):
    file_id = write_doc(docs_dir, text="body", title="Old")
    session = MarkdownSession(file_id)

    assert session.get_title() == "Old"
    session.set_title("New Title")

    assert session.get_title() == "New Title"
    assert get_title_from_path(docs_dir / f"{file_id}.crdt") == "Old"


def test_keystroke_insert_backspace_and_delete(docs_dir):
    async def scenario():
        session = MarkdownSession(write_doc(docs_dir, text="abcd"))
        session.add_member("alice")
        session.add_member("bob")
        session.indexes["bob"] = 4

        added = await session.keystroke("alice", {"op": "add", "index": 1, "char": "XY"})
        assert added["markdown"] == "aXYbcd"
        assert added["indexes"]["alice"] == 3
        assert added["indexes"]["bob"] == 6
        assert 'data-sid="alice"' in added["html"]
        assert 'data-sid="bob"' in added["html"]

        deleted = await session.keystroke("alice", {"op": "backspace", "index": 3})
        assert deleted["markdown"] == "aXbcd"
        assert deleted["indexes"]["alice"] == 2
        assert deleted["indexes"]["bob"] == 5

        removed = await session.keystroke("alice", {"op": "delete", "index": 1, "count": 2})
        assert removed["markdown"] == "acd"
        assert removed["indexes"]["alice"] == 1
        assert removed["indexes"]["bob"] == 3

    asyncio.run(scenario())


def test_delete_clamps_to_the_end_and_pulls_cursors_inside_the_range(docs_dir):
    async def scenario():
        session = MarkdownSession(write_doc(docs_dir, text="abcd"))
        session.add_member("alice")
        session.add_member("bob")
        session.indexes["alice"] = 1
        session.indexes["bob"] = 3

        result = await session.keystroke("alice", {"op": "delete", "index": 1, "count": 50})

        assert result["markdown"] == "a"
        assert result["indexes"]["alice"] == 1
        assert result["indexes"]["bob"] == 1

    asyncio.run(scenario())


def test_keystroke_rejects_bad_payloads(docs_dir):
    async def scenario():
        session = MarkdownSession(write_doc(docs_dir, text="ab"))
        session.add_member("alice")

        assert await session.keystroke("alice", "nope") == {"ok": False}
        assert await session.keystroke("alice", {"op": "add", "index": "start", "char": "Z"}) == {"ok": False}
        assert await session.keystroke("alice", {"op": "add", "index": 0, "char": ""}) == {"ok": False}
        assert await session.keystroke("alice", {"op": "nope", "index": 0}) == {"ok": False}
        assert await session.keystroke("alice", {"op": "backspace", "index": 0}) == {"ok": False}
        assert await session.keystroke("alice", {"op": "delete", "index": 2, "count": 1}) == {"ok": False}
        assert await session.keystroke("alice", {"op": "delete", "index": 0, "count": "many"}) == {"ok": False}
        assert parse_doc_to_md_str(session.doc) == "ab"

    asyncio.run(scenario())


def test_cursor_moves_and_clamps_for_members_only(docs_dir):
    async def scenario():
        session = MarkdownSession(write_doc(docs_dir, text="abcd"))
        session.add_member("alice")

        moved = await session.cursor("alice", {"index": 2})
        assert moved["indexes"]["alice"] == 2
        assert "markdown" in moved and "html" in moved

        clamped = await session.cursor("alice", {"index": 99})
        assert clamped["indexes"]["alice"] == 4

        negative = await session.cursor("alice", {"index": -5})
        assert negative["indexes"]["alice"] == 0

        assert await session.cursor("stranger", {"index": 1}) == {"ok": False}
        assert await session.cursor("alice", ["nope"]) == {"ok": False}
        assert await session.cursor("alice", {"index": "end"}) == {"ok": False}

    asyncio.run(scenario())


def test_broadcast_clamps_stale_indexes(docs_dir):
    async def scenario():
        session = MarkdownSession(write_doc(docs_dir, text="hi"))
        session.add_member("alice")
        session.indexes["alice"] = 40

        state = await session.broadcast()

        assert state["markdown"] == "hi"
        assert state["indexes"] == {"alice": 2}
        assert 'data-sid="alice"' in state["html"]

    asyncio.run(scenario())


def test_marker_escapes_session_id(docs_dir):
    session = MarkdownSession(write_doc(docs_dir, text="hi"))

    assert 'data-sid="a&quot;b&lt;c&gt;"' in session._marker('a"b<c>')


def test_concurrent_inserts_both_land(docs_dir):
    async def scenario():
        session = MarkdownSession(write_doc(docs_dir, text=""))
        session.add_member("alice")
        session.add_member("bob")

        await asyncio.gather(
            session.keystroke("alice", {"op": "add", "index": 0, "char": "X"}),
            session.keystroke("bob", {"op": "add", "index": 0, "char": "Y"}),
        )

        assert sorted(parse_doc_to_md_str(session.doc)) == ["X", "Y"]

    asyncio.run(scenario())


def test_save_persists_edits_and_title(docs_dir):
    async def scenario():
        file_id = write_doc(docs_dir, text="old", title="Old")
        session = MarkdownSession(file_id)
        session.add_member("alice")
        await session.keystroke("alice", {"op": "add", "index": 3, "char": "!"})
        session.set_title("Renamed")

        assert await session.save_doc() == {"ok": True}

        loaded = get_doc_copy_from_path(docs_dir / f"{file_id}.crdt")
        assert parse_doc_to_md_str(loaded) == "old!"
        assert get_title_from_path(docs_dir / f"{file_id}.crdt") == "Renamed"

    asyncio.run(scenario())


def test_save_failure_raises(docs_dir, monkeypatch):
    async def scenario():
        session = MarkdownSession(write_doc(docs_dir, text="hi"))

        def explode(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("controller.markdown_session.save_doc_to_path", explode)

        with pytest.raises(SaveDocumentError):
            await session.save_doc()

    asyncio.run(scenario())


def test_delete_doc_removes_the_crdt_file(docs_dir):
    file_id = write_doc(docs_dir, text="gone")
    session = MarkdownSession(file_id)
    path = docs_dir / f"{file_id}.crdt"

    session.delete_doc()

    assert not path.exists()
    with pytest.raises(DeleteDocumentError):
        session.delete_doc()


def test_connect_and_disconnect_update_membership(docs_dir):
    async def scenario():
        session = MarkdownSession(write_doc(docs_dir, text="hi"))

        await session.connect("alice")
        assert session.indexes["alice"] == 2

        with pytest.raises(DuplicateMemberError):
            await session.connect("alice")

        await session.disconnect("alice")
        assert session.indexes == {}

        with pytest.raises(MemberNotFoundError):
            await session.disconnect("alice")

    asyncio.run(scenario())


def test_title_keystroke_leaves_the_document_cursor(docs_dir):
    async def scenario():
        session = MarkdownSession(write_doc(docs_dir, text="hello", title="Plan"))
        session.add_member("alice")
        session.add_member("bob")

        renamed = await session.title_keystroke("alice", {"op": "add", "index": 4, "char": "!"})

        assert session.get_title() == "Plan!"
        assert renamed["title"] == "Plan!"
        assert renamed["title_index"] == {"alice": 5, "bob": None}
        assert renamed["indexes"]["alice"] is None
        assert renamed["indexes"]["bob"] == 5
        assert 'data-sid="alice"' not in renamed["html"]
        assert 'data-sid="bob"' in renamed["html"]

        session.title_index["bob"] = 4
        await session.title_keystroke("alice", {"op": "add", "index": 4, "char": "s"})
        assert session.get_title() == "Plans!"
        assert session.title_index["alice"] == 5
        assert session.title_index["bob"] == 5
        assert session.indexes["bob"] == 5

        back = await session.keystroke("alice", {"op": "add", "index": 5, "char": "!"})
        assert back["markdown"] == "hello!"
        assert back["indexes"]["alice"] == 6
        assert back["indexes"]["bob"] == 6
        assert back["title_index"]["alice"] is None
        assert back["title_index"]["bob"] == 5

    asyncio.run(scenario())


def test_opening_a_missing_file_raises(docs_dir):
    from bson import ObjectId

    with pytest.raises(FileNotFoundError):
        MarkdownSession(ObjectId())
