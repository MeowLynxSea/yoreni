# Common Gotchas

## Channel dependencies for strict type checking

Channel dependencies come from channel package manifests, outside the main extras. Reproduce CI's setup with `uv sync --all-extras --dev`, then `uv run --no-sync python -m scripts.install_channel_dependencies --all-channels`. Keep `--no-sync` on subsequent `uv run` commands so syncing does not remove those dependencies. See `.github/workflows/ci.yml` for checks.

## Channel-owned UI and WebUI transport

Channels can own frontend code in `yoreni/channels/*/webui/` and tests in `yoreni/channels/*/tests/`, including `tests/webui/`. The WebUI's lint and test configurations include those frontend paths. Do not assume all channel tests live under the root `tests/` or all frontend code lives under `webui/`.

Vite proxies `/api`, `/webui`, and `/auth`; the application's WebSocket connects directly to the gateway. `YORENI_API_URL` sets the HTTP proxy target, whose default is `http://127.0.0.1:8765`. See `webui/vite.config.ts`.

## Config `${VAR}` References

`yoreni/config/loader.py` loads and validates the config; `resolve_config_env_vars` resolves `${VAR}` references before runtime use. This is not shell-like default-value syntax. Missing referenced variables raise `ConfigLoadError` with field locations; invalid existing config files also raise `ConfigLoadError`. Defaults are created when the config file is absent, not as recovery from an invalid file.

## Windows Compatibility

yoreni explicitly supports Windows. Key differences to keep in mind:
- `ExecTool` defaults to PowerShell on Windows (`pwsh` when available, otherwise Windows PowerShell); pass `shell="cmd"` for cmd.exe syntax or cmd built-ins (`shell.py`).
- `yoreni/cli/entry.py` configures Windows console output as UTF-8 before dispatch to handle emoji and multilingual input.
- MCP stdio server commands are normalized for Windows path separators (`mcp.py`).

## Prompt Templates

Agent system prompts and scenario-specific instructions live in `yoreni/templates/`, including `agent/identity.md` and `agent/platform_policy.md`; workspace defaults include `AGENTS.md`, `HEARTBEAT.md`, and `SOUL.md`. Prompt rendering is handled by `yoreni/utils/prompt_templates.py`. Changing these files alters agent behavior as directly as changing Python code.

The repository's root `AGENTS.md` guides coding agents; `yoreni/templates/AGENTS.md` is a default for yoreni workspaces. `yoreni/agent/context.py` loads project `AGENTS.md` and global `SOUL.md`/`USER.md`, skipping unchanged bundled `AGENTS.md` and `USER.md` defaults. It does not implement Codex's ancestor-directory instruction chain.

Tool descriptions, skills, and replayed session history also shape model behavior. Treat changes to those surfaces like runtime code: keep them focused, verify the affected contract, and avoid teaching the model to repeat internal markers, local paths, or tool-call text. Add a regression test when it proves a reachable failure that existing coverage does not address.

## Context Pollution Persists

Anything written into memory, session history, or prompt inputs can be replayed into future LLM calls. Metadata such as timestamps, local media paths, tool-call echoes, and raw fallback dumps must be bounded and sanitized before they become examples for the model to imitate.

## Skills as Extension Point

Built-in skills live in `yoreni/skills/` (markdown + YAML frontmatter format). Agent capabilities that are "know-how" rather than code should be added as skills, not hardcoded into the agent loop. External skills can be published to and installed from ClawHub.

## Persistence and durability

`MemoryService` (`yoreni/memory/service.py`) owns long-term memory. Evidence, facts, FTS indexes, vectors, jobs and Dream state live in `memory/memory.sqlite3`; `SOUL.md`, `USER.md` and `memory/MEMORY.md` are its only file surface. Writes that change a fact's meaning run inside one `BEGIN IMMEDIATE` transaction with their sources and index rows. Never open that database with another schema, write those files from a tool, or bypass `FactStore`.

Agent identity, Event records, Run state, checkpoints, and recovery are stored in `agent.sqlite3`. `AgentStore` (`yoreni/agent/state.py`) commits runtime mutations in SQLite transactions using WAL, foreign keys, and `PRAGMA synchronous = FULL` on every connection. Keep state updates behind the AgentStore owner and preserve transaction boundaries, event deduplication, and revision checks. Existing `sessions.sqlite3` files are left untouched and are not imported.

## Memory configuration

Long-term memory is configured under `memory` in `config/schema.py`. `embedding` is optional: when it is absent, retrieval is keyword-only and `memory_search` reports `semantic_index: false`. When present it must name a `model` and either an OpenAI-compatible `api_base` or a configured provider whose endpoint and key are reused.

```yaml
memory:
  enabled: true
  embedding:
    provider: openai
    model: text-embedding-3-small
    dimensions: 1536
  core_token_budget: 3000
  retrieval_token_budget: 4000
  retrieval_limit: 8
  extraction_batch: 10
  dream_batch_facts: 60
  dream_backlog_threshold: 20
```

`core_token_budget` and `retrieval_token_budget` bound the always-visible profile and the per-turn memory block. Changing the embedding `model` creates a separate vector set: `MemoryService.backfill` notices the new index, counts what is missing, and re-embeds it in the background while the Home "Memory update" widget shows progress. The task state lives in `memory_meta`, so a gateway restart resumes from the same place, and a second model change drops the old task instead of carrying its progress over.

The same fields are editable in the WebUI under Settings → Capabilities → Long-term memory. The dialog reuses configured provider credentials by default; an API key entered there is stored in the local config and returned only as a masked hint. Saving applies immediately: the gateway reloads the agent, swaps the embedder, and starts the memory update task when the index changed.

## Emoji artwork

`webui/scripts/copy-emoji-assets.mjs` copies the Twemoji SVGs used by the emoji catalogue into `webui/public/emoji/` and writes `manifest.json`; the directory is gitignored and regenerated by `bun run dev` and `bun run build`. Emoji outside the manifest keep the platform glyph, so a new dataset never produces a broken image. `yoreni/web/dist` therefore carries roughly 8 MB of artwork that must be rebuilt with the bundle.
