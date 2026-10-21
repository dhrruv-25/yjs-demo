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
import re
import asyncio

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "documents"
COLORS = ("#7aa2f7", "#9ece6a", "#f7768e", "#bb9af7", "#7dcfff")
BR_BEFORE = re.compile(r"<br\s*/?\s*>[ \t]*$", re.IGNORECASE) 

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
        self.lock = asyncio.Lock()


    def delete_doc(self) -> None:
        try:
            os.remove(DOCS_DIR / f"{self.file_id}.crdt")
        except:
            raise DeleteDocumentError


    def save_doc(self) -> None:
        try:
            save_doc_to_path(self.doc, DOCS_DIR / f"{self.file_id}.crdt")
        
        except:
            raise SaveDocumentError

    def drop_member(self, member_id: str) -> None:
        try:
            self.indexes.pop(member_id)
        except:
            raise MemberNotFoundError
        
        if self.indexes.keys().__contains__(member_id):
            raise DuplicateMemberError


    def add_member(self, member_id: str) -> None:
        if self.indexes.keys().__contains__(member_id):
            raise DuplicateMemberError
        
        else:
            self.indexes[member_id] = len(self.doc.get("md_content", type=Array).to_py())


    def get_members(self) -> List[str]:
        
        return self.indexes.keys()


    def get_title(self) -> str:
        title = parse_md_arr_to_md_str(self.doc.get("md_title", type=Array).to_py())
        return title
    

    def set_title(self, title: str) -> None:
        self.doc.set("md_title", Array(title))
        save_doc_to_path(self.doc, DOCS_DIR / f"{self.file_id}.crdt")
    

    def _apply_keystroke(self, op: str, index: int, char: str, count: int = 1) -> None: 
        """
            Applies a keystroke operation to the document.
        """
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


    def _shift_for_delete(self, author: str, start: int, count: int) -> None: 
        """
        Shifts the cursor position of other users after a delete operation.
        * This function edits the index object in session memory. *
        """
        indexes = self.get_indexes()
        end = start + count
        for sid, pos in indexes.items():
            if sid == author:
                continue
            if pos >= end:
                indexes[sid] = pos - count
            elif pos > start:
                indexes[sid] = start
        indexes[author] = start
    

    def _with_line_breaks(self, text: str) -> tuple[str, list[int]]:
        """
        Put a <br> before each newline that does not already have one.
        Renders the 2D page into a 1D line and maps the position of each character by jumping positions in the 1D line at every linebreak.
        """
        text = parse_doc_to_md_str(self.doc)
        pieces: list[str] = []
        mapping = [0]
        size = 0
        for index, char in enumerate(text):
            if char == "\n" and not BR_BEFORE.search(text[:index]): # check if char is /n and whether the text uptill {:index} has a <br> at its end. Basically if a user asks for a new line inline
                pieces.append("<br>")
                size += 4
            pieces.append(char)
            size += 1
            mapping.append(size)
        return "".join(pieces), mapping
    

    def _marker(sid: str) -> str:
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
        indexes = self.get_indexes()
        placed = []
        for sid, pos in indexes.items():
            pos = max(0, min(pos, len(text)))
            placed.append((mapping[pos], sid))
        for at, sid in sorted(placed, reverse=True):
            chars.insert(at, self._marker(sid))
        return parse_md_str_to_html("".join(chars))

    
    async def keystroke(self, sid: str, data: dict) -> dict:
        """
        Handles a keystroke operation.
        * This function edits the index object in session memory. *
        """
        async with self.lock:
            if not isinstance(data, dict):
                return {"ok": False}
            try:
                index = int(data.get("index", 0))
            except (TypeError, ValueError):
                return {"ok": False}
            index = max(0, min(index, len(parse_doc_to_md_str(self.doc))))
            op = data.get("op")
            char = data.get("char") or ""
            if op == "backspace" and index > 0:
                self._apply_keystroke("backspace", index, "")
                self.shift_others(sid, index, -1)
                self.indexes[sid] = index - 1
            elif op == "delete":
                try:
                    count = int(data.get("count", 0))
                except (TypeError, ValueError):
                    return {"ok": False}
                count = max(0, min(count, len(parse_doc_to_md_str(self.doc)) - index)) #user can delete everything to the right of the cursor
                if count <= 0:
                    return {"ok": False}
                self._apply_keystroke("delete", index, "", count)
                self._shift_for_delete(sid, index, count)
            elif op == "add" and isinstance(char, str) and char:
                self._apply_keystroke("add", index, char)
                self.shift_others(sid, index, len(char))
                self.indexes[sid] = index + len(char)
            else:
                return {"ok": False}
            await self.broadcast()
            return {"ok": True}
        

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
            await self.broadcast()
            return {"ok": True}



    async def broadcast(self) -> None:
        """
        Broadcasts the document to all users in the session.
        * This function edits the index object in session memory. *
        """
        text = parse_doc_to_md_str(self.doc)
        indexes = self.get_indexes()
        for sid in indexes:
            indexes[sid] = max(0, min(indexes[sid], len(text)))
        return {"html": self._html_with_carets(text), "markdown": text, "indexes": dict(self.indexes)}
