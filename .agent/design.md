# Design Constraints

Consult this document when changing ownership boundaries, extension points, abstractions, or dynamic types. These constraints apply to implementation choices on those paths.

## Core stays small; extend at the edges

New capabilities should be added via `channels/`, `tools/`, skills, or MCP servers. The files `agent/loop.py` and `agent/runner.py` form the critical core path; changes there should be minimal and justified. If a feature can live in a channel adapter, a tool, or an external MCP server, it should not be inlined into the agent loop.

Runtime state fan-out follows the same boundary. `MessageBus.publish` awaits local subscribers for turn/run/model/goal state changes; `MessageBus.publish_event` queues routed channel delivery without waiting for network sends. Both carry `AgentEvent` values. Runner hooks publish typed output through the turn's scoped `EventSink`; direct-call callbacks are adapted at the execution boundary. WebUI/WebSocket wire details and goal-state sync belong in the relevant WebUI or channel adapter. `yoreni.webui.outbound_delivery.WebUIOutboundDelivery` owns WebUI outbound projection, stream buffers, Run output and delivery completion; the WebSocket transport only snapshots recipients and queues its public frames, retaining connection ordering, backpressure and drain accounting.

Tools and channels own their configuration, metadata, and activation behavior. Tool configuration models live beside their implementations in `agent/tools/*_config.py`; channel descriptors and configuration stay in their channel packages. The root schema in `config/schema.py` assembles these contracts without importing their runtime implementations. Tool classes expose their configuration through `config_key` and `config_cls()`.

Generic recovery checkpoints belong in `agent/recovery.py`; WebUI recovery and Run coordination belong in the Agent/WebUI event path. Gateway startup supplies WebUI Run policy. Channels own the pure address contract in `channels/notification_routes.py`, which core turn delivery consumes for default replies and durable notifications without loading channel runtime implementations.

## Executable import boundaries

Core execution, Agent persistence, and security primitives stay independent of API, channel, CLI, and WebUI adapter implementations. Core may consume channel-owned pure address contracts, which must stay independent of runtime implementations. Shared client contracts stay independent of UI implementations, and client initialization dependencies must be acyclic.

Python rules live in [scripts/check_import_boundaries.py](../scripts/check_import_boundaries.py); run `python -m scripts.check_import_boundaries` from the repository root. Client rules live in [scripts/check_client_import_boundaries.mjs](../scripts/check_client_import_boundaries.mjs); run `bun run check:imports` and `bun run test:imports` from `webui/`. These scripts are the source of truth for checked modules, dependency rules, and exceptions.

The checks cover selected direct imports and client initialization dependencies. They do not establish a complete runtime dependency graph; execution, state ownership, and security contracts still require behavioral verification.

## Prefer duplication over premature abstraction

Channels and providers are allowed to repeat similar logic (send retries, media handling, message splitting). Do not introduce complex base classes or shared helpers just to eliminate duplication across channel files. Each channel file should remain self-contained and readable on its own. The same applies to provider implementations.

## Type dynamic boundaries at the edge

Wire payloads, persisted records, and third-party SDK objects are untrusted dynamic boundaries. Prefer a parser or small normalizer at the owning edge, and use `TypedDict` for stable dictionary shapes, so validation happens once and internal code receives a concrete type. Do not spread raw dynamic dictionaries or SDK objects through the core.

Stable first-party dependencies must be typed where they are stored or passed. Do not declare an internal service, context field, or callback result as `Any` and then recover its real type with consumer-side casts. Use the concrete type or a narrow `Protocol`; reserve `Any` for genuinely dynamic boundaries.

Every new cast must be supported by a boundary check or an invariant established by construction and control flow. Do not add redundant checks inside a validated path or use `cast` only to silence BasedPyright.

## Explicit over magical

Provider auto-detection must remain traceable from the factory to the concrete provider class.

## Long-term memory ownership

`yoreni/memory/` is the only long-term memory implementation. `MemoryService` owns evidence, facts, indexes, the job queue, Dream state, and the core files (`SOUL.md`, `USER.md`, `memory/MEMORY.md`). Agent tools, Run completion, context assembly, and consolidation call the service; none of them opens `memory/memory.sqlite3` or writes a core file directly.

Memory is owner-scoped: only the personal agent principal reads or writes it, and project visibility is expressed through fact `scope` (`global` or `project:<name>`). Retrieval filters by scope, status, kind, subject and validity before ranking, so one project cannot see another project's local facts.

Evidence is durable before interpretation: a completed Run is written as `evidence` rows with deterministic IDs, and extraction runs later as a queued job. Candidate facts must cite stored evidence; the store reconciles them by provenance (`user_statement`, `user_correction`, `tool_result`, `external_document`, `agent_inference`, `unverified`) rather than by model confidence. Single-valued facts version by dedupe key; unclear conflicts stay `disputed`.

Retrieval fuses FTS5 and exact SQLite vector similarity with Reciprocal Rank Fusion. `ContextBuilder` renders only a bounded core-memory summary; per-turn retrieval reaches the model through a `RuntimeContextBlock` labelled as stored data, never as instructions.

Dream is a single bounded model call producing a JSON change set. Validation, application, core-file writes, the Git commit, and the cursor advance are separate durable steps recorded in `dream_runs`, so recovery can resume without applying a change twice. Dream never receives file or shell tools and cannot write `SOUL.md` or install a skill; skill proposals stay pending for independent review.

`memory/memory.sqlite3` is created and migrated by `MemoryService`; the one-shot legacy import lives in `yoreni/memory/migration.py` and aborts startup on any inconsistency rather than running with partial data.
