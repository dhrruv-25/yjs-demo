Goal: Design a CRDT framework with Yrs via pyCRDT to have users collaboratively edit a document, and have offline functionality which automerges with the cloud Doc upon reconnection. [Current implementation document type: Markdown]

Desging Choices:

    General:
        - Asynchronous Implementation. 
        - Leaderless replication. 
        - Scaleable; should work across multiple instances and should allow for agentic tool call integration. 

    Offline session availability:
        - For offline capability, the client must keep its own copy of the markdown that is updated from backend upon every emit while connected to maintain latest versions. 
        - Upon losing socket connection, client updates its own copy of the markdown, which is transacted on the cloud doc upon reconnection. 
    
    Relay Networks and InMemoryDocs:
        - All users on a particular doc form a relay network among themselves. 
        - Every socket_id gets an InMemoryDoc. A user may have multiple socket_ids.
        - RelayNetworks maintain a set of documents that are to be applied to. They also maintain an async queue of (doc_id, bytes).
        - If a document is already in the set of documents to be applied to, then its changes are simply added to the queue. 
        - Transactions (bytes) are key'd by sid's and queued in RelayNetworks. Each instance of the app maintains their own relay network for the same document if needed.
        - RelayNetworks also store the sid's in session of the member on other instances also, transactions issued by them are also added to the queue.
            - Changes across instances are maintained by Redis pub/sub and socketio rooms. 
    
    InMemoryDocs:
        - InMemoryDocs are copied from iDrive upon initialisation, if the file_id already exists.
        - Every InMemoryDoc maintains a buffer of TransactionDTOs, that is drained into doc transaction history.
            - These buffers may be drained either after a select number of transacations, or by time interval. 
                - When the client is still connected to the socket, every keystroke is a transaction. However, it may happen that a user goes offline mid session and then comes back and enact a large transaction. 
                - Alternatively, the client may maintain a history of keystrokes that may be sent to the server upon reconnecting that then executes transactions atomically. 
        - Use pyCRDT sticky indexes for indices. (Not super necessary)
        - Add users to RelayNetworks for sharing transactions and maintaining a uniform state while connected.
        - An InMemoryDoc's buffer only contains TransactionDTOs of the transactions executed by only its own user_id, and session_id.  
        - InMemoryDocs also must maintain the docs' state. 
        
    CRDT Object Datastructure:
        - Every markdown is really just a .crdt file/ Doc() object. 
        - Every doc() contains an 'md' Array. Each element in the array is a Text object that symbolizes a line in the markdown. An empty line is an empty Text object.
        - Every Doc() also contains a Map (namespace: 'history') that stores its transaction history, key'd by str(user_id; time). 

    TransactionDTOs:
        - Contain Transaction events, user and session details as well as undo flags. 
        - Transaction times are also stored. 

    Undo's and Redo's:
        - Undo's may unobserve all transactions that aren't themselves undo transactions. 
        - Undo's must not unobserve any changes made by another session_id. 
        - Redo's may only undo undo transactions and nothing else.
            - Redo's also must not work if the most recent transaction made by that session_id is not itself an undo. 
        - Undo's and Redo's must not persist after session has stopped.      




Still to be done:
- How to drain the buffer? 
    - Do we drain through InMemoryDoc or RelayNetwork? 
    - How do we compress transactions? Over keystrokes or time deltas?
    - Currnently there is no persistence requirement on undos and redos.