import asyncio

import pytest
from bson import ObjectId

from controller.markdown_session import MarkdownSession
from controller.sessions_manager import SessionsManager
from operations.helper import get_doc_copy_from_path, parse_doc_to_md_str
from schemas.exceptions import (
    FileAlreadyExistsError,
    SessionNotEmptyError,
    SessionNotFoundError,
    UserAlreadyConnectedError,
    UserNotConnectedError,
)
from tests.conftest import write_doc


def _manager():
    return SessionsManager()


def test_first_user_opens_a_session_and_second_user_joins_it(docs_dir):
    async def scenario():
        file_id = write_doc(docs_dir, text="hello")
        manager = _manager()

        first = await manager.connect_user("alice", file_id)
        second = await manager.connect_user("bob", file_id)

        assert first is second
        assert isinstance(first, MarkdownSession)
        assert set(first.get_members()) == {"alice", "bob"}
        assert await manager.get_user_session("alice") is first
        assert manager.user_sessions == {"alice": file_id, "bob": file_id}

    asyncio.run(scenario())


def test_connect_rejects_a_user_who_is_already_in_a_session(docs_dir):
    async def scenario():
        first = write_doc(docs_dir, text="one")
        second = write_doc(docs_dir, text="two")
        manager = _manager()
        await manager.connect_user("alice", first)

        with pytest.raises(UserAlreadyConnectedError):
            await manager.connect_user("alice", first)
        with pytest.raises(UserAlreadyConnectedError):
            await manager.connect_user("alice", second)

        assert set(manager.sessions) == {first}

    asyncio.run(scenario())


def test_missing_file_and_unknown_user(docs_dir):
    async def scenario():
        manager = _manager()

        with pytest.raises(FileNotFoundError):
            await manager.connect_user("alice", ObjectId())
        with pytest.raises(UserNotConnectedError):
            await manager.get_user_session("alice")
        with pytest.raises(SessionNotFoundError):
            await manager._get_markdown_session(ObjectId())

    asyncio.run(scenario())


def test_edits_are_shared_and_saved_when_the_last_user_leaves(docs_dir):
    async def scenario():
        file_id = write_doc(docs_dir, text="hi")
        manager = _manager()
        session = await manager.connect_user("alice", file_id)
        await manager.connect_user("bob", file_id)

        await session.keystroke("alice", {"op": "add", "index": 2, "char": "!"})
        assert session.indexes["alice"] == 3
        assert session.indexes["bob"] == 3

        await manager.drop_user_session("alice")

        assert "alice" not in manager.user_sessions
        assert file_id in manager.sessions
        assert set(session.get_members()) == {"bob"}
        with pytest.raises(UserNotConnectedError):
            await manager.get_user_session("alice")

        await manager.drop_user_session("bob")

        assert manager.sessions == {}
        assert manager.user_sessions == {}
        loaded = get_doc_copy_from_path(docs_dir / f"{file_id}.crdt")
        assert parse_doc_to_md_str(loaded) == "hi!"

        reopened = await manager.connect_user("carol", file_id)
        assert parse_doc_to_md_str(reopened.doc) == "hi!"
        assert set(reopened.get_members()) == {"carol"}

    asyncio.run(scenario())


def test_create_rejects_an_existing_session_and_delete_rejects_members(docs_dir):
    async def scenario():
        file_id = write_doc(docs_dir, text="hi")
        manager = _manager()
        await manager._create_markdown_session(file_id, "alice")

        with pytest.raises(FileAlreadyExistsError):
            await manager._create_markdown_session(file_id, "bob")
        with pytest.raises(SessionNotEmptyError):
            await manager._delete_session(file_id)

        assert file_id in manager.sessions
        assert set((await manager.get_user_session("alice")).get_members()) == {"alice"}

    asyncio.run(scenario())


def test_drop_unknown_user_raises(docs_dir):
    async def scenario():
        manager = _manager()
        with pytest.raises(UserNotConnectedError):
            await manager.drop_user_session("nobody")

    asyncio.run(scenario())
