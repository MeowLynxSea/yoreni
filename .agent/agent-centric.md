# Agent-centric runtime

Yoreni is a single persistent personal Agent. Conversation Session is not a product or execution abstraction. The supported runtime concepts are Agent identity and state, durable Event records, and independent Run lifecycles.

## Runtime boundary

```text
WebUI / API / SDK / internal trigger
                 |
             AgentInput
                 |
       AgentLoop.process_event
                 |
       PersistentAgent + AgentStore
                 |
   ContextBuilder -> AgentRunner -> tools
                 |
       Run result, checkpoint, Event output
```

`AgentStore` owns the stable Agent identity, revisioned state, event admission and deduplication, Run records, checkpoints, recovery state, and memory-journal outbox. Events preserve source, principal, kind, destination, and correlation data. Retry admission is keyed by the source and event ID; a conflicting payload is rejected rather than executed twice.

`PersistentAgent` builds bounded model context from Agent state, the current Event, selected recent user Runs, long-term memory, and task state. Complete Event and Run records remain queryable and recoverable without loading all history into a model prompt. `AgentRunner` remains the mature execution engine for model calls, tools, hooks, sandboxes, streaming, and checkpoints.

User input, scheduled work, heartbeat, Dream, and local triggers all submit explicit AgentInput events. Trusted personal events may read private state; untrusted external events retain their channel permissions and cannot obtain the personal Agent transcript by supplying an identifier.

Runs have independent IDs, lanes, leases, cancellation, checkpoints, and recovery. User interaction and background work are not serialized through a global conversation lock. Mutating tool effects and optimistic Agent-state updates retain coordination so concurrent Runs cannot silently overwrite each other.

Ephemeral execution is a Run property. It uses the same Runner and tools without creating a durable Event or Run, and it cannot be resumed from storage. Persistent Runs survive restart as queued or interrupted records and can be explicitly resumed.

## Interfaces and persistence

The current API and SDK accept `AgentInput`; obsolete `session_id`, SessionClient, session routing, and Session snapshots are not supported. The WebUI protocol is version 2 and exposes one Yoreni entry point with live Event and Run frames. It does not create, list, switch, delete, fork, or paginate chat Sessions.

New runtime state is stored in `agent.sqlite3` in Agent, Events, and Runs tables. Existing `sessions.sqlite3`, backups, and personal files are not deleted or automatically imported. The old Session package, Session stores, Session keys, unified-session behavior, and Session compatibility adapters have been removed.

Technical uses of the word session remain only where they describe an external or independent mechanism, such as `ExecSession`, HTTP/MCP client sessions, channel login sessions, provider protocol headers, prompt-toolkit sessions, or operating-system process options. None owns Agent identity, memory, Event history, or Run lifecycle.

## Engineering rules

- Keep Agent state, Event admission, Run lifecycle, memory, and recovery in their owning Agent modules.
- Reuse `AgentRunner`, ContextBuilder, tool registry, sandbox, MemoryService, hooks, and output adapters instead of duplicating them.
- Validate source, principal, destination, and event payloads at ingress; do not infer authority from a client-supplied identifier.
- Keep complete history durable for audit and recovery, but select bounded context for each model call.
- Do not reintroduce a Session-shaped compatibility layer under another name.
