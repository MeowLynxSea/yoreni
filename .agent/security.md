# Security Boundaries

Consult this document when changing path access, outbound HTTP/MCP, or shell execution. The agent operates with filesystem, shell, and network access; preserve the guards below on every affected path.

## Workspace Restriction

Filesystem tools (`read_file`, `write_file`, `edit_file`, `list_dir`, `apply_patch`) resolve paths through the workspace path resolver (`agent/tools/filesystem.py` / `agent/tools/path_utils.py`), which enforces that the resolved path must lie under the active workspace when workspace restriction is enabled. The media upload directory is always an internal extra read root while restricted.

Additional filesystem roots must be capability-specific. `extra_allowed_dirs` is a legacy read-only alias. Use `extra_read_allowed_dirs` for read-only roots, `extra_write_allowed_dirs` only when a write-capable tool is intentionally allowed to modify an extra directory, and exact file allowlists when a tool may modify only specific files.

Shell execution (`ExecTool`, `agent/tools/shell.py`) also respects `restrict_to_workspace` as an application-level guard: if enabled and `working_dir` is outside the workspace, the command is rejected before execution, and command text is checked for obvious workspace escapes. This is not process-level isolation; use an exec sandbox backend for that.

State ownership is separate from the access mode: writes to core memory files, the active config file, and the owner profile and onboarding files are refused on every agent path, including full access. Only `MemoryService` consolidation, `UserProfileStore` and the settings writers touch them. The guard lives in `ExecTool._guard_state_writes` and `_FsTool._refuse_memory_write`; trusted operator shells (`ExecTool.execute_to_completion`) are exempt.

Project workspaces tighten this boundary: a turn whose scope names a project always resolves to `<workspace>/projects/<id>` in restricted mode, ignoring any client-requested path or access mode, and the project directory is validated against the projects root (including the `uploads` subdirectory) so a symlink cannot redirect project writes outside it. Deleting a project re-checks the directory before removing any record, so a redirected path stops the operation while the data is intact.

**Rule**: Any new path-handling logic must go through the workspace path resolver or perform an equivalent containment check with explicit read/write capability semantics.

## SSRF Protection

All outbound HTTP requests from agent tools must pass through the shared URL guards in `security/network.py` (`validate_url_target` or `resolve_url_target`). By default they block loopback, RFC1918 private addresses, CGNAT ranges, link-local ranges, and cloud metadata endpoints (including `169.254.169.254`).

For direct requests, the only escape hatch is `configure_ssrf_whitelist(cidrs)`, which reads from `config.tools.ssrf_whitelist` at load time. The shared `network.proxy` address enabled by `providers.<name>.useProxy` is a separate user-authorized trust boundary for provider requests and provider-returned image URL downloads. Those downloads still reject malformed URLs and locally identifiable private/internal targets on every redirect, but hostnames unavailable to local DNS are delegated to the trusted proxy. The user-selected proxy owns final DNS resolution and network egress policy.

HTTP/SSE MCP transports are part of this boundary: validate configured MCP URLs before probing or constructing clients, and validate each outgoing HTTP request before redirects are followed. Local/private HTTP MCP endpoints are allowed only through the explicit SSRF whitelist. Stdio MCP servers are not part of the HTTP SSRF path.

**Rule**: Do not add direct `httpx.get` / `requests.get` calls in tools. Route through the existing web fetch utilities or replicate the `validate_url_target` check.

## Shell Sandbox

`tools/sandbox.py` provides optional command wrapping: `bwrap` (bubblewrap) on Linux and `seatbelt` (`sandbox-exec`) on macOS. Seatbelt must not expose shared host temporary directories; scratch stays in the workspace. On Windows a configured backend warns and leaves only the application guard. On Unix a configured backend that cannot start must fail, not silently execute without isolation.

**Rule**: If adding a sandbox backend, match the backend callable contract in `yoreni/agent/tools/sandbox.py` and register it in `_BACKENDS`. Preserve read/write root handling and the configured backend's failure behavior.
