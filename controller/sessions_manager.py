from controller.markdown_session import MarkdownSession
from pydantic import BaseModel
from typing import Dict
from bson import ObjectId
from schemas.exceptions import (
    FileAlreadyExistsError,
    FileNotFoundError,
    UserNotFoundError,
    SessionNotFoundError,
    UnknownError,
    DuplicateMemberError,
    SessionNotEmptyError
)
import asyncio
'''
SessionManager to manage multiple MarkdownSessions(). Contains 2 objects only:
    - user_sessions: Dict[str, ObjectId] - maps user_id to file_id
    - sessions: Dict[ObjectId, MarkdownSession] - maps file_id to MarkdownSession

SessionManager.sessions contains copies of Docs() imported from memory. 
Therefore, sessions must be re-saved before dropping from memory. Sessions must 
also autosave after every 10 seconds of inactivity.


Methods:
    - create_markdown_session(file_id: ObjectId, user_id: str) -> MarkdownSession:
        - Creates a new MarkdownSession if it doesn't exist.
        - Adds the user to the session.
        - Returns the session.
    - _get_markdown_session(file_id: str) -> MarkdownSession:
        - Returns the session if it exists.
        - Raises FileNotFoundError if the session doesn't exist.
    - get_user_session(user_id: str) -> MarkdownSession:
        - Returns the session if it exists.
'''



class SessionsManager:
    def __init__(self):
        self.user_sessions: Dict[str, ObjectId] = {str(ObjectId()): ObjectId('6ad658148c6ec8fbafa2da50')}
        self.sessions: Dict[ObjectId, MarkdownSession]
        self.lock = asyncio.Lock()


    async def _create_markdown_session(self, file_id: ObjectId, user_id: str) -> MarkdownSession:
        async with self.lock:
            if self.sessions.get(file_id) is not None: #file already exists
                    raise FileAlreadyExistsError

            else:
                session = MarkdownSession(file_id)
            
            self.user_sessions[user_id] = file_id
            session.add_member(user_id)
            self.sessions[file_id] = session
            return self._get_user_session(user_id)


    def _get_markdown_session(self, file_id: str) -> MarkdownSession:
        if self.sessions.get(file_id) is None:
            raise FileNotFoundError
        
        return self.sessions.get(file_id)


    async def get_user_session(self, user_id: str) -> MarkdownSession:
        file_id = self.user_sessions.get(user_id)

        if file_id is None:
            raise FileNotFoundError
        
        return self._get_markdown_session(file_id)
    

    async def drop_user_session(self, user_id: str) -> None:
            file_id = self.user_sessions.get(user_id)
            delete_session = len(await self.get_user_session(user_id).get_members()) == 1
            await self.get_user_session(user_id).drop_member(user_id)
            self.user_sessions.pop(user_id)

            if delete_session:
                await self._delete_session(file_id)


    async def _delete_session(self, file_id: str) -> None:
        async with self.lock:
            session = self._get_markdown_session(file_id)

            if len(session.indexes.keys()) > 0:
                raise SessionNotEmptyError
            
            self.sessions.pop(file_id)