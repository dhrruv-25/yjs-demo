Goal: Design a CRDT framework with Yrs via pyCRDT to have users collaboratively edit a document, and have offline functionality which automerges with the cloud Doc upon reconnection. [Current implementation document type: Markdown]

Desging Choices:

    General:
        - Asynchronous Implementation. 
        - Leaderless replication. 
        - Scaleable; should work across multiple instances and should allow for agentic tool call integration. 

    CRDT Object Datastructure:
        - Every markdown is really just a .crdt file/ Doc() object. 
        - Every Doc() contains a single 'md' Text object holding the whole markdown. Lines are derived by splitting on '\n'.
        - Transaction history is stored outside the doc, in a sidecar documents/<file_id>.history.jsonl, one entry per line key'd by str(user_id; time).
        - Cursors are not stored in the doc. They are ephemeral Awareness state (y-protocols), where each client publishes {user, cursor: {anchor, head}} with anchor/head as Yjs relative positions (pyCRDT StickyIndex JSON). A session may only publish awareness for its own client ids.

    TransactionDTOs:
        - Contain Transaction events, user and session details as well as undo flags. 
        - Transaction times are also stored. 

    Offline session availability:
        - For offline capability, the client must keep its own copy of the crdt document (markdown) that is updated from backend upon every emit while connected to maintain latest versions. 
        - Upon losing socket connection, client updates its own crdt copy, which is transacted on the cloud doc upon reconnection. 
    
    InMemoryDocs:
        - InMemoryDocs are copied from iDrive upon initialisation, if the file_id already exists. This implementation simply takes from disk.
        - There is one InMemoryDoc per file_id per instance: the single server replica every session reads and writes through. It also owns the file's Awareness.
        - Cursor positions are resolved server-side with pyCRDT StickyIndexes (GET /api/files/{id}/cursors).

    Sessions:
        - Every socket_id gets a Session. A user may have multiple socket_ids.
        - Every Session maintains a buffer of TransactionDTOs, that is drained into the history file.
            - These buffers may be drained either after a select number of transacations, or by time interval. 
                - When the client is still connected to the socket, every keystroke is a transaction. However, it may happen that a user goes offline mid session and then comes back and enact a large transaction. 
        - A Session's buffer only contains TransactionDTOs of the transactions executed by its own user_id and session_id, and only those that actually changed the doc.
        - Emits are sent from inside Sessions. Doc updates are batched per event-loop tick and merged.

    Relay Networks:
        - All sessions editing a file_id form a RelayNetwork around that file's InMemoryDoc.
        - A session's update is applied to the InMemoryDoc and fanned out to every other session in the network. An sid never receives its own updates.
        - Awareness updates are fanned out the same way. When a session leaves, its awareness states are removed and the removal is broadcast.
        - Each instance of the app maintains its own relay network for the same document if needed.
            - Changes across instances are maintained by Redis pub/sub and socketio rooms (not implemented yet).
    
    
    Undo's and Redo's:
        - Undo's may unobserve all transactions that aren't themselves undo transactions. 
        - Undo's must not unobserve any changes made by another session_id. 
        - Redo's may only undo undo transactions and nothing else.
            - Redo's also must not work if the most recent transaction made by that session_id is not itself an undo. 
        - Undo's and Redo's must not persist after session has stopped.      

    frontend:
        - For the current implementation, the frontend only needs to show a list of files in the documents/ directory.
            - trash icons next file for DELETE.
            - plus icons to CREATE a file.
        - Every file in directory gets an endpoint. Files may be retrieved RESTfully. 
            - every file endput must also have a save button, executed via PUT.

        - The frontend maintains its own crdt document that forms a Relay *only* with its backend counterpart. The backend copy may be a part of another, bigger, RelayNetwork. 
        - The frontend-backend Relay is bridged by socketIO for client-server operations, and Redis pub/sub for cross instance fanout in the backend. 

    Socket Events:
        - Since the backend and frontend maintain their own docs, the only events and emits are transactions and awareness. 
        - `sync`: handshake. Client state vector in; missing update, server state vector and all awareness states out.
        - `update`: binary doc update, with undo/redo flags when sent by the UndoManager.
        - `awareness`: binary y-protocols awareness update.
        - `render`: server -> room, rendered HTML.

    Implemented (single instance; run with `uvicorn V2.app:app --port 8001`):
        - Undo/Redo: client-side Y.UndoManager tracking only local-origin transactions, so it never touches other sessions and does not outlive the page.
        - Text operations: the textarea diff of each input event becomes one insert/delete transaction on the 'md' Text.
        - Buffer draining: RelayNetwork drains every V2_DRAIN_SECONDS (0 disables, letting the buffer grow), on save, on GET history and on leave. Consecutive DTOs with the same undo/redo flags are merged with merge_updates into one history entry.
        - md renderer from v1 (server side); markdown-it in the browser while offline.
        - V1 documents (markdown_arr) are migrated into the 'md' Text on first load.

Still to be done:
- Offline edits only survive while the page stays open (no IndexedDB persistence yet).
- Redis pub/sub cross-instance fan-out.
- Consider CodeMirror + y-codemirror.next in place of the textarea now that the doc is a single Text.