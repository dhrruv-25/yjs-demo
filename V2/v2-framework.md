Goal: Design a CRDT framework with Yrs via pyCRDT to have users collaboratively edit a document, and have offline functionality which automerges with the cloud Doc upon reconnection. [Current implementation document type: Markdown]

Desging Choices:

    General:
        - Asynchronous Implementation. 
        - Leaderless replication. 
        - Scaleable; should work across multiple instances and should allow for agentic tool call integration. 

    CRDT Object Datastructure:
        - Every markdown is really just a .crdt file/ Doc() object. 
        - Every doc() contains an 'md' Array. Each element in the array is a Text object that symbolizes a line in the markdown. An empty line is an empty Text object.
        - Every Doc() also contains a Map (namespace: 'history') that stores its transaction history, key'd by str(user_id; time). 
        - Every Doc() also contains a Map (namesapce: 'cursor') that maintains where other user's indexes (StickyIndex) are in the document. A user may only operate on their own sid's index. 

    TransactionDTOs:
        - Contain Transaction events, user and session details as well as undo flags. 
        - Transaction times are also stored. 

    Offline session availability:
        - For offline capability, the client must keep its own copy of the crdt document (markdown) that is updated from backend upon every emit while connected to maintain latest versions. 
        - Upon losing socket connection, client updates its own crdt copy, which is transacted on the cloud doc upon reconnection. 
    
    InMemoryDocs:
        - InMemoryDocs are copied from iDrive upon initialisation, if the file_id already exists. This implementation simply takes from disk.
        - Every socket_id gets an InMemoryDoc. A user may have multiple socket_ids.
        - Every InMemoryDoc maintains a buffer of TransactionDTOs, that is drained into doc transaction history.
            - These buffers may be drained either after a select number of transacations, or by time interval. 
                - When the client is still connected to the socket, every keystroke is a transaction. However, it may happen that a user goes offline mid session and then comes back and enact a large transaction. 
        - Use pyCRDT StickyIndexes for indices. Indexes are nested inside crdt objects, a sid must only operate on its own index. 
        - An InMemoryDoc's buffer only contains TransactionDTOs of the transactions executed by only its own user_id, and session_id.  
        - Emits are sent from insde InMemoryDocs.

    Relay Networks and InMemoryDocs:
        - All users editing an InMemoryDoc that share a file_id form a RelayNetwork to transact among themselves. 
        - RelayNetworks maintains and executes an async queue of (doc_id, bytes) everytime a document makes a change.
        - If a document is already in queue, it may not append to the queue until its caught up with others' transactions. If it's not in queue, it may add to it. 
        - Transactions (bytes) are key'd by sid's and queued in RelayNetworks. Each instance of the app maintains their own relay network for the same document if needed.
        - RelayNetworks also stores sid's in session of members on other instances also, transactions issued by them are also added to the queue.
            - Changes across instances are maintained by Redis pub/sub and socketio rooms. 
        - An sid cannot publish to itself, and hence cannot add itself to the queue.
    
    
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
        - Since the backend and frontend maintain their own docs, the only events and emits are transactions. 

    Implemented (single instance; run with `uvicorn V2.app:app --port 8001`):
        - Socket events: `sync` (handshake: client state vector in, missing update + server state vector out), `update` (binary, with undo/redo flags), `render` (server -> room).
        - Per socketID StickyIndexes: cursor[sid] = {user_id, color, line, col}, where line/col are encoded sticky indexes (Yjs relative positions decode with pyCRDT StickyIndex). Foreign cursor writes are reverted; cursors are removed on disconnect and cleared when a file is first opened.
        - Undo/Redo: client-side Y.UndoManager tracking only local-origin transactions, so it never touches other sessions and does not outlive the page.
        - Array operations: the textarea diff of each input event becomes one transaction on the Array<Text> line model (split on Enter, merge on Backspace at column 0).
        - Buffer draining: InMemoryDoc owns its buffer; RelayNetwork drains every V2_DRAIN_SECONDS (0 disables, letting the buffer grow), on save and on leave. Consecutive DTOs with the same undo/redo flags are merged with merge_updates into one history entry.
        - md renderer from v1 (server side); markdown-it in the browser while offline.
        - V1 documents (markdown_arr) are migrated into 'md' lines on first load.

Still to be done:
- History lives inside the synced doc, so every client downloads it. Consider moving it to a separate doc/file.
- Offline edits only survive while the page stays open (no IndexedDB persistence yet).
- Redis pub/sub cross-instance fan-out.