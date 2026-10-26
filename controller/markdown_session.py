from pycrdt import Doc, Array
from pydantic import BaseModel, Field
from typing import List, Dict
from bson import ObjectId
from schemas.exceptions import (
    MemberNotFoundError, 
    DuplicateMemberError,
    MemberIndexMismatchError,
    SaveDocumentError,
    DeleteDocumentError
)
from operations.helper import (
    get_doc_copy_from_path,
    parse_md_arr_to_md_str, 
    save_doc_to_path, 
    parse_doc_to_md_arr,
    parse_doc_to_md_str,
    parse_md_str_to_html
)
from pathlib import Path
from html import escape
import os
import asyncio

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "documents"
COLORS = ("#7aa2f7", "#9ece6a", "#f7768e", "#bb9af7", "#7dcfff") 

'''
 Notes:  ** This MarkdownSession is used to load existing markdown documents into session memory. **
 - Session.session_members should be dropped for an indexes dict whose keys() are all active session members.
 - New session member indices intialize at the end of the document.
 - Perhaps make doc title mandator

 Session methods:
    - delete_doc() -> None:
        - Deletes the document from memory.
        - Raises DeleteDocumentError if the document does not exist.
    - save_doc() -> None:
        - Saves the document to memory.
        - Raises SaveDocumentError if save fails.
    - drop_member(member_id: str) -> None:
        - Drops a member from the session.
        - Raises MemberNotFoundError if the member does not exist.
        - Raises DuplicateMemberError if member persists after drop.
    - add_member(member_id: str) -> None:
        - Adds a member to the session.
        - Raises DuplicateMemberError if member already exists.
    - get_members() -> List[str]:
        - Returns the list of members in the session.
    - get_title() -> str:
        - Returns the title of the document.
    - set_title(title: str) -> None:
        - Sets the title of the document.
        - Raises SetTitleError if set fails.
        - Returns the indexes of the members in the session.
    - _apply_keystroke(op: str, index: int, char: str, count: int = 1) -> None:
        - Applies a keystroke operation to the document.
        - Raises ApplyKeystrokeError if apply fails.
    - _shift_for_delete(author: str, start: int, count: int) -> None:
        - Shifts the cursor position of other users after a delete operation.
        - Raises ShiftForDeleteError if shift fails.
    - _with_line_breaks(text: str) -> tuple[str, list[int]]:
'''

class MarkdownSession():

    def __init__(self, file_id: ObjectId):
        super().__init__()
        self.file_id = file_id
        self.doc = get_doc_copy_from_path(DOCS_DIR / f"{file_id}.crdt")
        self.indexes = {}
        self.title_index = {}
        self.lock = asyncio.Lock()


    def drop_member(self, member_id: str) -> None:
        try:
            self.indexes.pop(member_id)
        except:
            raise MemberNotFoundError
        self.title_index.pop(member_id, None)
        
        if self.indexes.keys().__contains__(member_id):
            raise DuplicateMemberError


    def add_member(self, member_id: str) -> None:
        if self.indexes.keys().__contains__(member_id):
            raise DuplicateMemberError
        
        else:
            self.indexes[member_id] = len(parse_doc_to_md_arr(self.doc))
            self.title_index[member_id] = None


    def get_members(self) -> List[str]:
        
        return self.indexes.keys()


    def get_title(self) -> str:
        title = parse_md_arr_to_md_str(self.doc.get("md_title", type=Array).to_py())
        return title
    

    def set_title(self, title: str) -> None:
        arr = self.doc.get("md_title", type=Array)
        with self.doc.transaction():
            arr.clear()
            if title:
                arr.extend(list(title))


    def _apply_keystroke(self, op: str, index: int, char: str, count: int = 1, arr: Array = None) -> None: 
        """
            Applies a keystroke operation to the document.
        """
        if arr is None:
            arr = parse_doc_to_md_arr(self.doc)
        index = max(0, min(index, len(arr)))
        with self.doc.transaction():
            if op == "backspace":
                if index > 0:
                    del arr[index - 1]
                return
            if op == "delete":
                count = max(0, min(count, len(arr) - index))
                for offset in range(count - 1, -1, -1):
                    del arr[index + offset]
                return
            for offset, ch in enumerate(char):
                arr.insert(index + offset, ch)


    def _shift_for_delete(self, author: str, start: int, count: int, positions: dict = None) -> None: 
        """
        Shifts the cursor position of other users after a delete operation.
        * This function edits the index object in session memory. *
        """
        if positions is None:
            positions = self.indexes
        end = start + count
        for sid, pos in positions.items():
            if sid == author or pos is None:
                continue
            if pos >= end:
                positions[sid] = pos - count
            elif pos > start:
                positions[sid] = start
        positions[author] = start
    

    def _with_line_breaks(self, text: str) -> tuple[str, list[int]]:
        """
        Keep the markdown source intact and map each cursor index onto that source.
        Single newlines become <br> when the markdown renderer runs.
        """
        return text, list(range(len(text) + 1))
    

    def _marker(self, sid: str) -> str:
        """
        Adds a colored caret to the document to indicate the cursor position of each user
        """
        color = COLORS[sum(sid.encode()) % len(COLORS)]
        return f'<span class="caret" data-sid="{escape(sid, quote=True)}" style="--c:{color}"></span>'


    def _html_with_carets(self, text: str) -> str:
        """
        Adds carets to the document to indicate the cursor position of each user
        """
        expanded, mapping = self._with_line_breaks(text)
        chars = list(expanded)
        placed = []
        for sid, pos in self.indexes.items():
            if pos is None:
                continue
            pos = max(0, min(pos, len(text)))
            placed.append((mapping[pos], sid))
        for at, sid in sorted(placed, reverse=True):
            chars.insert(at, self._marker(sid))
        return parse_md_str_to_html("".join(chars))

    def _shift_others(self, author: str, at: int, delta: int, positions: dict = None) -> None:
        """
        Shifts the cursor position of other users after a keystroke operation.
        * This function edits the index object in session memory. *
        """
        if positions is None:
            positions = self.indexes
        for sid, pos in positions.items():
            if sid != author and pos is not None and pos >= at:
                positions[sid] = max(0, pos + delta)

    
    async def _keystroke_locked(self, sid: str, data: dict, arr: Array, positions: dict, other: dict) -> dict:
        if not isinstance(data, dict):
            return {"ok": False}
        try:
            index = int(data.get("index", 0))
        except (TypeError, ValueError):
            return {"ok": False}
        index = max(0, min(index, len(arr)))
        op = data.get("op")
        char = data.get("char") or ""
        if op == "backspace" and index > 0:
            self._apply_keystroke("backspace", index, "", arr=arr)
            self._shift_others(sid, index, -1, positions)
            positions[sid] = index - 1
        elif op == "delete":
            try:
                count = int(data.get("count", 0))
            except (TypeError, ValueError):
                return {"ok": False}
            count = max(0, min(count, len(arr) - index))
            if count <= 0:
                return {"ok": False}
            self._apply_keystroke("delete", index, "", count, arr)
            self._shift_for_delete(sid, index, count, positions)
        elif op == "add" and isinstance(char, str) and char:
            self._apply_keystroke("add", index, char, arr=arr)
            self._shift_others(sid, index, len(char), positions)
            positions[sid] = index + len(char)
        else:
            return {"ok": False}
        other[sid] = None
        return await self.broadcast()


    async def keystroke(self, sid: str, data: dict) -> dict:
        """
        Handles a keystroke operation.
        * This function edits the index object in session memory. *
        """
        async with self.lock:
            return await self._keystroke_locked(
                sid, data, parse_doc_to_md_arr(self.doc), self.indexes, self.title_index
            )


    async def title_keystroke(self, sid: str, data: dict) -> dict:
        """
        Handles a keystroke in the title. The author leaves the document cursor.
        """
        async with self.lock:
            return await self._keystroke_locked(
                sid, data, self.doc.get("md_title", type=Array), self.title_index, self.indexes
            )
        

    async def cursor(self, sid: str, data: dict) -> dict:
        """
        Handles a cursor operation.
        * This function edits the index object in session memory. *
        """
        async with self.lock:
            if sid not in self.indexes or not isinstance(data, dict):
                return {"ok": False}
            try:
                index = int(data.get("index", 0))
            except (TypeError, ValueError):
                return {"ok": False}
            text = parse_doc_to_md_str(self.doc)
            self.indexes[sid] = max(0, min(index, len(text)))
            self.title_index[sid] = None
            return await self.broadcast()
            #return {"ok": True}


    async def broadcast(self) -> None:
        """
        Broadcasts the document to all users in the session.
        * This function edits the index object in session memory. *
        """
        text = parse_doc_to_md_str(self.doc)
        title = self.get_title()
        for sid, pos in self.indexes.items():
            if pos is not None:
                self.indexes[sid] = max(0, min(pos, len(text)))
        for sid, pos in self.title_index.items():
            if pos is not None:
                self.title_index[sid] = max(0, min(pos, len(title)))
        return {
            "html": self._html_with_carets(text),
            "markdown": text,
            "indexes": dict(self.indexes),
            "title": title,
            "title_index": dict(self.title_index),
        }

    
    async def save_doc(self) -> None:
        async with self.lock:
            try:
                save_doc_to_path(self.doc, DOCS_DIR / f"{self.file_id}.crdt")
                return {"ok": True}
            except:
                raise SaveDocumentError
    
    async def connect(self, sid: str) -> None:
        async with self.lock:
            self.add_member(sid)
            await self.broadcast()

    
    async def disconnect(self, sid: str) -> None:
        async with self.lock:
            self.drop_member(sid)
            await self.broadcast()