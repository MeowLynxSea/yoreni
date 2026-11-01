# Connectors, accounts, and credential protection

`yoreni/credentials/` is the only path through which a stored secret can reach a tool or leave the process. Agent code sees account metadata and placeholders; the model API never sees a real value.

## Vocabulary and ownership

A **connector** is an authentication definition (`ConnectorSpec`): allowed methods, required fields, scopes, and optional OAuth endpoints. Built-in connectors cover API keys, basic auth, one-time codes, public services, and MCP servers; operators can register more connectors, which are persisted beside accounts.

An **account** is one user's connection through a connector, including whether its secrets may be restored into tool calls and whether it is session-only. An **authentication request** is the structured card the agent asks for; it carries the connector, method, field names, purpose, the requested account label and lifetime, and the requesting Run. A **credential** is one field of an account's encrypted payload.

`CredentialManager` is the single entry point: it owns the encrypted store, the in-memory secret vault, the connector catalog, and pending authentication requests. WebUI routes and agent tools call it; neither touches `credentials.sqlite3` or the key file directly.

## Storage and encryption

Accounts live in `credentials.sqlite3` beside the instance config. Payloads are AES-GCM sealed with associated data equal to the account ID, so ciphertext cannot be moved between accounts. The master key is 32 random bytes in `keys/credentials.key` with owner-only permissions, or supplied by `YORENI_CREDENTIAL_KEY` for managed deployments. Rotation rewraps every payload in one transaction before the new key file replaces the old one.

One-time fields (verification codes) are never written to the database. They stay in memory, expire quickly, and are dropped after the first successful restoration. OAuth access and refresh tokens are stored but are never restorable into a tool call.

A session-only account (the agent asked for `ephemeral`, or every field of the method is a one-time code) is held in memory and never written to the database. It stays usable for the life of the process and disappears on restart, so it has no representation in `credentials.sqlite3` and no entry in Settings → Accounts. Master-key rotation rebuilds the vault from stored accounts and therefore drops session-only accounts and one-time codes. An OAuth request cannot be session-only, because the tokens exist to be refreshed and reused from storage.

## Placeholders and redaction

Every registered secret maps to a deterministic placeholder `{{YORENI_SECRET_<16 hex>}}` derived with HMAC, so the same value yields the same token across rounds, streams, and restarts. Redaction runs at the provider boundary (`LLMProvider._safe_chat` and `_safe_chat_stream`) and on every direct model call in memory, extraction, and chat search. It rewrites strings anywhere in the message tree, including system prompts, history, tool-call arguments, reasoning fields, and tool results.

Redaction fails closed: after rewriting, the vault asserts that no known secret survives, and a failure becomes a provider error instead of an outbound request. High-confidence untrusted shapes (`sk-…`, GitHub tokens, AWS keys, private-key blocks, long bearer tokens) are also replaced, without a mapping, so they can never be restored.

Provider API keys are registered as redaction-only secrets at gateway startup. Legacy MCP OAuth tokens in `auth/mcp.json` are imported into the encrypted store for redaction; the MCP client keeps owning its file, and a failed read changes nothing.

## Tool restoration

Restoration happens in `agent/tools/execution.py` after `before_execute_tool` and immediately before `tool.execute`, so hooks, events, transcripts, and checkpoints only ever see the placeholder arguments.

Any tool can receive a credential. `CredentialManager.restore_arguments` checks only that each placeholder belongs to an account that is connected, unexpired, and marked restorable, then asks `SecretVault.restore_value` to substitute the value anywhere in the argument tree. There is no tool allowlist, no argument-field allowlist, and no destination check: submitting a card is the user's authorization, and the model decides where the value goes. Shell, exec, HTTP, messaging, and filesystem tools are not excluded.

One-time codes stop restoring after their first use, but their mapping stays available for redaction. Revoking or expiring an account removes restoration while keeping redaction.

Agents receive placeholders from `request_authentication` and `list_connections`; these are opaque references, never secrets. `list_connections` is the agent's view of existing accounts: label, connector, method, status, and whether an account is session-only. Tool output, including errors and exceptions, is redacted before it enters the model context, run record, memory, or WebUI activity detail.

## Authentication cards and OAuth

`request_authentication` creates a persisted auth request and blocks the Run. The card asks only for the connector and its fields; nothing is inferred from the current page. The agent supplies the account label and whether it is session-only, and the WebUI may replace the label on submit. `CredentialManager.request_auth` decides the lifetime the request will actually have — a connector whose fields are all one-time codes is session-only whatever the agent asked for — and the card states that decision in its footer. The agent's `purpose` sentence describes the service and the reason, never the retention, because it is model prose and the card would otherwise promise something the gateway does not do. The gateway pushes an `auth_request` frame; the WebUI renders a card and submits values through `/api/webui/credentials/submit`, never through chat or MCP elicitation. Submission seals the payload, marks the account restorable, resolves the waiting future thread-safely, and the Run continues.

A request does not expire. It stays pending until the user answers it or the Run that asked for it is cancelled; `request_authentication` waits without a deadline, and a `CancelledError` cancels the request so the card disappears with the Run.

Settings → Accounts (`webui/src/components/settings/accounts/`) is the operator surface for stored records. It lists only connectors that exist in the runtime — API key and username/password today, plus any connector the operator registered — and groups them by connection kind. Session-only accounts are not listed: they have no record to manage and disappear with the process. It offers connect, manage, and disconnect. MCP authorization, one-time codes, and public endpoints stay in their own surfaces. There are deliberately no vendor presets: a service appears here only after its integration and OAuth client actually exist, so the page never advertises a connection that cannot complete.

OAuth uses authorization code with PKCE and a server-side `state`. The browser is redirected to the provider; the provider redirects to `/auth/connectors/callback`; the gateway validates state, exchanges the code, stores tokens, and resumes the requesting Run. Callbacks must be HTTPS or loopback, so a remote deployment needs a TLS reverse proxy. The browser never receives a third-party token.

## Boundaries and limits

The single guarantee is that a stored secret never enters a model request. Redaction enforces it at the provider boundary, fails closed, and covers every model call the process makes. It detects the literal value; a value the model transforms first — base64, a split string, a re-encoding — is no longer the registered secret and will pass through. Treat that as the residual risk of the design.

Any tool that receives a placeholder sees the real value at execution time, so the model can send it anywhere a tool can reach. Nothing restricts which tool or destination gets it.

Generic OAuth uses public-client PKCE (no client secret). Connectors whose provider requires a confidential client secret are not supported yet.
