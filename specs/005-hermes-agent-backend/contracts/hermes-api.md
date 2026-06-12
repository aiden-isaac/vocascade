# Contract: Hermes Agent API-Server Adapter (consumed subset)

**Status**: Drafted from upstream source/docs research (2026-06-11). Fields
marked ⚠️ must be **verified against a live server in Phase 0 (T101)** and this
file updated — upstream (`NousResearch/hermes-agent`,
`gateway/platforms/api_server.py`) may evolve.

## Connection

- Base URL: `HERMES_BASE_URL`, e.g. `http://jarlaxle:8642/v1` (Tailscale) or
  `http://localhost:8642/v1`. Upstream defaults: `API_SERVER_HOST=127.0.0.1`,
  `API_SERVER_PORT=8642` — remote deployments must bind the Tailscale interface.
- Auth: `Authorization: Bearer <API_SERVER_KEY>` on every request. If the
  server has no key configured it skips auth entirely (**test-only**; never
  expose beyond localhost/Tailscale).
- Headers sent on every request:
  - `X-Hermes-Session-Key: <HERMES_SESSION_KEY>` — stable; scopes long-term
    memory; ≤256 chars; requires API-key auth. ⚠️ verify accepted on `/v1/runs`.
  - `X-Hermes-Session-Id: <uuid4>` — per voice session; conversational
    continuity.
- Limits: 10 MB body; 65,536 chars per content part; idempotency cache 300 s.

## Endpoints consumed

### `GET /health`
200 ⇒ reachable. Used by `check_hermes.py` and startup probe.

### `GET /v1/capabilities`
Machine-readable feature discovery. We require only: does the runs API exist
(⚠️ exact field name/shape — capture real payload in T101). Probe failure ⇒
chat-fallback mode.

### `POST /v1/runs`
Dispatch asynchronous run. Returns **202** immediately.

```jsonc
// request (⚠️ verify exact accepted fields)
{ "input": "<user request text>", "model": "hermes-agent" }
// response
{ "run_id": "<id>", "status": "queued|running" }   // ⚠️ verify field names
```

### `GET /v1/runs/{run_id}`
Run snapshot for reconciliation: status + result when terminal.
⚠️ capture real status vocabulary and result field in T101.

### `GET /v1/runs/{run_id}/events`  (SSE)
"Structured lifecycle events"; keepalives every 30 s. Expected kinds mapped to
`RunEventKind`:

| Upstream (⚠️ verify names) | Mapped kind | Adapter action |
|---|---|---|
| accepted / started | `ACCEPTED` | task → `executing` |
| progress / tool activity | `PROGRESS` | log only (v1) |
| approval required | `APPROVAL_REQUIRED` | enqueue `APPROVAL_REQUEST` delivery |
| completed | `COMPLETED` | extract result → enqueue `RESULT` |
| failed / error | `FAILED` | task → `failed`; enqueue `FAILURE_NOTICE` |
| (anything else) | `UNKNOWN` | log + skip, never fatal |

Disconnect handling: reconnect w/ backoff (1 s → 60 s cap); after any gap,
reconcile via `GET /v1/runs/{run_id}` so terminal events are never lost.

### `POST /v1/runs/{run_id}/stop`
Cancel. Adapter marks task `cancelled` optimistically; a late terminal event
for a cancelled task is discarded.

### `POST /v1/runs/{run_id}/approval`
Resolve pending approval. ⚠️ verify body shape (`{"approved": true/false}` or
decision string) in T101.

### `POST /v1/chat/completions`  (fallback path only)
OpenAI-compatible; `stream: true` ⇒ SSE `data:` lines with
`choices[0].delta.content`, terminated by `data: [DONE]`. Already implemented in
`voice_satellite/gateway/hermes_client.py` (note: base URL carries `/v1`).
Used when capabilities indicate no runs API. Session headers as above.

## Not consumed (explicitly out of scope)

`/v1/responses*`, `/api/sessions*` (CRUD/fork/per-session chat), `/v1/models`
(informational only), webhook adapter, any non-API-server gateway platform.

## Contract test (T101)

`tests/contract/test_hermes_api.py` — skipped unless `HERMES_BASE_URL` is
reachable. Asserts: health 200; capabilities parse; run dispatch → 202 +
run_id; events stream yields a terminal event for a trivial prompt; `get_run`
agrees with the stream; stop on a fresh run yields a cancelled/stopped status;
auth required when key configured (401 without bearer). Each ⚠️ above is
resolved and this document updated with the observed payloads.
