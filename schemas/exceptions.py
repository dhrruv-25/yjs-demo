## for the markdown session

class MemberNotFoundError(Exception):
    message = "Member not found in session"

class DuplicateMemberError(Exception):
    message = "Member already exists in session"

class MemberIndexMismatchError(Exception):
    message = "Member index mismatch in session; num(indexes) != num(members)"

class SaveDocumentError(Exception):
    message = "Failed to save document"

class DeleteDocumentError(Exception):
    message = "Failed to delete document"





### for the sessions manager

class FileAlreadyExistsError(Exception):
    message = "File already exists"

class FileNotFoundError(Exception):
    message = "File not found"

class UserNotFoundError(Exception):
    message = "User not found"

class UnknownError(Exception):
    message = "Unknown error"

class SessionNotFoundError(Exception):
    message = "Session not found"

class SessionNotEmptyError(Exception):
    message = "Session is not empty"