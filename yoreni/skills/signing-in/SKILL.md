---
name: signing-in
description: Sign in to a website or service the user's tools need, using an authentication card and the placeholder it returns.
metadata: {"yoreni":{"emoji":"🔑"}}
---

# Signing in to a service

When a task needs an account — a campus portal, a web app, an API behind a login — the user signs in through a card in the chat. You never see the password, and you never ask for it in a message.

## The flow

1. `list_connections` to see what already exists. An account that is already connected comes with placeholders, so skip to step 4.
2. `request_authentication` with a one-sentence `purpose`, and with `allowed_tools` and `allowed_domains` naming exactly what the credential is for. The Run pauses while the card is open.
3. The user submits the card. The tool result is JSON with `placeholders`, one per field — `{{YORENI_SECRET_…}}`.
4. Call the tool that needs the credential, passing each placeholder as the argument value it belongs to (`password`, `api_key`, `code`, …). At execution the gateway swaps in the real secret. The model, the transcript, and the answer only ever see the placeholder.

## What to do right after the card is submitted

The placeholder **is** the usable value. Continue the same turn: call the tool immediately with it, then finish the task. Do not ask the user to confirm, paste anything, or repeat the secret in chat. Do not tell them a placeholder was returned — that is internal plumbing.

## Choosing the request

- Connectors: `api_key` (a token or bearer key), `basic_auth` (username and password), `one_time_code` (a verification code), `mcp_server` (OAuth for a remote MCP server). `list_connections` returns the exact ids and methods.
- A portal with a password form is `basic_auth`. If the user prefers a passwordless route and can only give a code, ask for the code with `one_time_code` instead of collecting their password.
- `allowed_tools` must list the tool that will use the credential. `allowed_domains` must list the target host, because every credential-bearing call has to name a destination and match it. Missing either one turns the follow-up call into a denial.
- A one-time code works once. Call the tool immediately after receiving it, and request a new code for each new attempt.

## When it does not work

- Denied use: the error names the reason — a tool or domain outside the grant, a disconnected account, or an argument that is not credential-shaped. Fix that and retry, or request a new card with wider grants. Never ask the user for the secret instead.
- Expired or cancelled: say so in one sentence and offer to open a new card.
- The user wants to revoke access: send them to Settings → Accounts; you cannot delete an account yourself.

## Talking about it

One short message is enough: what you are connecting and why. After it works, report the result, not the mechanism. Phrases like "placeholder", "credential store", or "restoration" mean nothing to the user.
