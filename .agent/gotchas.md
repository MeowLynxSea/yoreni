# Common Gotchas

## Channel dependencies for strict type checking

Channel dependencies come from channel package manifests, outside the main extras. Reproduce CI's setup with `uv sync --all-extras --dev`, then `uv run --no-sync python -m scripts.install_channel_dependencies --all-channels`. Keep `--no-sync` on subsequent `uv run` commands so syncing does not remove those dependencies. See `.github/workflows/ci.yml` for checks.

## Channel-owned UI and WebUI transport

Channels can own frontend code in `yoreni/channels/*/webui/` and tests in `yoreni/channels/*/tests/`, including `tests/webui/`. The WebUI's lint and test configurations include those frontend paths. Do not assume all channel tests live under the root `tests/` or all frontend code lives under `webui/`.

Vite proxies `/api`, `/webui`, and `/auth`; the application's WebSocket connects directly to the gateway. `YORENI_API_URL` sets the HTTP proxy target, whose default is `http://127.0.0.1:8765`. See `webui/vite.config.ts`.

## Config `${VAR}` References

`yoreni/config/loader.py` loads and validates the config; `resolve_config_env_vars` resolves `${VAR}` references before runtime use. This is not shell-like default-value syntax. Missing referenced variables raise `ConfigLoadError` with field locations; invalid existing config files also raise `ConfigLoadError`. Defaults are created when the config file is absent, not as recovery from an invalid file.

## Windows Compatibility

yoreni explicitly supports Windows as a host for the gateway. Key differences to keep in mind:
- Agent commands never run on the host: `ExecTool` sends them to the sandbox VM, whose shell is always bash/sh/zsh. Host platform only matters for the gateway process itself.
- `yoreni/cli/entry.py` configures Windows console output as UTF-8 before dispatch to handle emoji and multilingual input.
- MCP stdio server commands are normalized for Windows path separators (`mcp.py`).

## Execution plane

All agent execution — files, shell, code, packages, processes, browser, desktop — goes through `yoreni/sandbox/`, owned by the Python gateway. `boxlite` is a required dependency (all platforms except Windows, where the sandbox reports itself unsupported), so a plain `uv sync` / `pip install` is enough. The VM installs its own browser during provisioning.

- Linux needs `/dev/kvm`; the user running the gateway must be able to open it. BoxLite reports a clear error when KVM is missing — the gateway logs it, keeps the control plane running, and every execution tool fails explicitly. There is no host fallback.
- macOS uses Hypervisor.framework through BoxLite (`boxlite` wheels cover Apple Silicon and Intel).
- Windows has no BoxLite backend yet; `backend: "boxlite"` reports `SandboxUnavailableError` instead of degrading.
- The VM's CPU count and memory are creation options: boxlite ignores them on an existing box and has no resize call, so `tools.sandbox.cpus` / `memoryMib` are asked for in the first onboarding step (`yoreni/sandbox/resources.py` caps them by this host) and the VM is only created afterwards. `gateway_runtime` therefore skips its warm-up until onboarding is complete, and `SandboxGateway.rebuild` is the only way to apply a new size later: it backs `/workspace` up to the backend staging dir, deletes the box, creates it again and restores the workspace. Changing the values without a rebuild only affects the next VM.
- First run: onboarding starts the sandbox and polls `SandboxGateway.progress` (`yoreni/sandbox/progress.py`); `POST /api/onboarding/sandbox/install` is idempotent, and `/api/onboarding/complete` returns 409 until the VM is ready, so nobody reaches chat while the VM is still preparing. Stages are ordered and weighted (`starting`, `backup`, `image`, `packages`, `browser`, `restore`, `services`); `backup` and `restore` only appear in a rebuild, the image stage reports bytes from the boxlite home and the browser stage from the Brave download, while stages without a byte signal stay indeterminate. The same progress is served as `setup` on `/api/sandbox/status` so the WebUI can mask itself during a rebuild.
- The image is pulled by boxlite itself; `tools.sandbox.image` accepts any registry path, including a Docker Hub mirror (`docker.m.daocloud.io/linuxserver/webtop:ubuntu-lxqt`) or another desktop tag (`ubuntu-lxqt` is the default; `ubuntu-kde` is the heaviest and prettiest, `alpine-*` has no `apt` and cannot be provisioned). Changing it pulls a new image, so the first start is slow again, and a tag from a different desktop family keeps the stock look: Yoreni's wallpaper and session styling only apply to the desktops `_STYLE_DESKTOP` in `yoreni/sandbox/provision.py` knows about.
- Webtop images now boot a Wayland session (`PIXELFLUX_WAYLAND=true` inside the image). `yoreni/sandbox/boxlite_backend.py` passes `PIXELFLUX_WAYLAND=false` plus `SELKIES_MANUAL_WIDTH/HEIGHT`, because x11vnc, xdotool and the desktop services all need an X server, and the image would otherwise pick its own 1024x768 screen. `_STYLE_DESKTOP` would have nothing to capture on a Wayland session.
- The first start of a configured image provisions the VM (apt packages), so the VM needs network access and several minutes. Provisioning is idempotent: `_PROVISION` asks `dpkg-query` whether anything from `_DESKTOP_PACKAGES` is missing and skips the install when nothing is, but always installs the whole list — asking apt for less has dead-ended on dependency resolution. Node.js is installed separately and only for npx-based MCP servers, so a dead end there is logged to `/workspace/.yoreni/logs/provision.log` instead of failing the whole provision; the same file holds the apt output of a failed install. Nothing else runs there — the browser is installed by `yoreni/sandbox/browser_assets.py`. The runtime home is `<instance data dir>/sandbox` by default (override with `tools.sandbox.home_dir`), so boxlite's images, QCOW2 disks and database live beside the rest of the instance state instead of `~/.boxlite`; deleting it loses the desktop, browser profile and downloaded packages.
- Browser automation is BrowserSkill, not a host-side driver: the host downloads the pinned `bsk` CLI once into `~/.yoreni/tools/bsk/<version>/<arch>/` and copies it to `/usr/local/bin/bsk`; the bundled MV3 extension is copied to `/opt/yoreni/browser-skill/extension` and the browser loads it with `--load-extension`. The presented browser is Brave stable, downloaded once by the host into `~/.yoreni/tools/brave/<version>/<arch>/` and installed with `apt-get` inside the VM (its `brave-keyring` dependency only exists in Brave's own apt repository, which is often unreachable, so a small local placeholder package satisfies it); Playwright's Chrome for Testing build is only downloaded inside the VM when Brave could not be installed, so the common path never pays for it. Brave is used because it publishes both Linux architectures, while Chrome and Edge have no arm64 build and Ubuntu's Chromium is a snap. The VM runs `bsk daemon` as the desktop user with `BSK_HOME=/var/lib/yoreni/bsk`, so `bsk` commands from the shell tool and from an interactive terminal reach the same browser; the daemon's own files are private to that user, which is why it is not started as root. The assets and daemon state stay on the VM root filesystem (`/opt`, `/var/lib`) rather than in the agent's workspace: runtime files are not the agent's to edit, and a deployment that does mount `/workspace` would hide host `copy_in` results and reject the daemon's Unix socket. `BSK_HOME` and `BSK_AUTO_START=0` are set for every VM command and re-exported in `/etc/profile.d/yoreni-bsk.sh` for an interactive terminal.
- Tests: `tests/sandbox/test_vm_integration.py` only runs with `YORENI_SANDBOX_INTEGRATION=1`. Without it, `conftest.py` binds the gateway to `tests/support/local_backend.py`, which is test-only and never used in production.

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
