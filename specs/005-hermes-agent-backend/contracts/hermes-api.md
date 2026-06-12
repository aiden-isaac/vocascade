# Contract: Hermes Agent API-Server Adapter (consumed subset)

**Status**: **Pinned against a live server (2026-06-12, T101)** — jarlaxle,
`gateway/platforms/api_server.py`. All former ⚠️ marks resolved; payloads below
are observed, not assumed. Re-run `tests/contract/test_hermes_api.py` after any
upstream (`NousResearch/hermes-agent`) update.

## Connection

- Base URL: `HERMES_BASE_URL`, e.g. `http://jarlaxle:8642/v1` (Tailscale) or
  `http://localhost:8642/v1`. Upstream defaults: `API_SERVER_HOST=127.0.0.1`,
  `API_SERVER_PORT=8642` — remote deployments must bind the Tailscale interface.
- Auth: `Authorization: Bearer <API_SERVER_KEY>` on every request — **including
  `GET /v1/capabilities`** (observed 401 without bearer; only `/health` is
  open). If the server has no key configured it skips auth entirely
  (**test-only**; never expose beyond localhost/Tailscale).
- Headers sent on every request:
  - `X-Hermes-Session-Key: <HERMES_SESSION_KEY>` — stable; scopes long-term
    memory; ≤256 chars. ✅ Verified accepted on `/v1/runs` (echoed back in the
    response headers). **Requires API-key auth**: on an unauthenticated server
    the header is rejected so a local client can't hijack a memory scope.
  - `X-Hermes-Session-Id: <uuid4>` — per voice session; conversational
    continuity.
- Limits: 10 MB body; 65,536 chars per content part; idempotency cache 300 s;
  **max 10 concurrent runs** (`_MAX_CONCURRENT_RUNS`) ⇒ 429
  `rate_limit_exceeded` on overflow.

## Endpoints consumed

### `GET /health`

200 ⇒ reachable. No auth. Lives at the server **root** (base URL minus `/v1`).
Used by `check_hermes.py` and startup probe.

### `GET /v1/capabilities`

Observed payload (subset the adapter consumes):

```jsonc
{
  "object": "hermes.api_server.capabilities",
  "platform": "hermes-agent",
  "model": "hermes-agent",
  "auth": { "type": "bearer", "required": true },
  "runtime": { "mode": "server_agent", "tool_execution": "server", ... },
  "features": {
    "chat_completions": true,            // fallback path
    "chat_completions_streaming": true,
    "run_submission": true,              // ── the adapter's probe keys ──
    "run_status": true,
    "run_events_sse": true,
    "run_stop": true,
    "run_approval_response": true,
    "tool_progress_events": true,
    "approval_events": true,
    "session_continuity_header": "X-Hermes-Session-Id",
    "session_key_header": "X-Hermes-Session-Key",
    ...
  },
  "endpoints": { "runs": {"method": "POST", "path": "/v1/runs"}, ... }
}
```

**Probe rule**: runs API available ⇔ `features.run_submission &&
features.run_events_sse`. Probe failure (network, 401, or flags absent) ⇒
chat-fallback mode via `features.chat_completions`.

### `POST /v1/runs`

Dispatch asynchronous run. Returns **202** immediately.

Accepted request fields (validated from `_handle_runs` source):

```jsonc
{
  "input": "<user request text>",   // REQUIRED; string or OpenAI-style message array
                                    //   (array ⇒ last entry = user message, rest = history)
  "model": "hermes-agent",          // optional, informational (echoed in snapshot)
  "instructions": "...",            // optional ephemeral system prompt
  "conversation_history": [          // optional [{role, content}]; wins over
    {"role": "user", "content": "…"} //   previous_response_id
  ],
  "previous_response_id": "...",    // optional, responses-API continuity
  "session_id": "..."               // optional; defaults to run_id
}
```

```jsonc
// response — observed
{ "run_id": "run_<32-hex>", "status": "started" }
```

The `"started"` in the 202 body is a literal; the snapshot vocabulary below is
authoritative. Errors: 400 missing `input` / invalid JSON, 401 no bearer,
429 over concurrency limit.

### `GET /v1/runs/{run_id}`

Run snapshot for reconciliation. Observed payload:

```jsonc
{
  "object": "hermes.run",
  "run_id": "run_6771f706…",
  "status": "completed",
  "created_at": 1781266110.78,        // epoch float
  "updated_at": 1781266130.25,
  "session_id": "run_6771f706…",      // = run_id unless session_id was sent
  "model": "hermes-agent",
  "last_event": "run.completed",
  "output": "pong",                   // present when terminal
  "usage": { "input_tokens": 20286, "output_tokens": 22, "total_tokens": 20308 }
}
```

**Status vocabulary** (from `_set_run_status` call sites, terminal states
bold): `queued` → `running` → (`waiting_for_approval` → `running`)* →
`stopping` → **`completed`** | **`failed`** | **`cancelled`**.
404 `run_not_found` for unknown ids.

### `GET /v1/runs/{run_id}/events`  (SSE)

`data: <json>` lines, each with an `event` discriminator; keepalive comment
`: keepalive` every 30 s; final comment `: stream closed` then EOF.

Observed/source-pinned event names mapped to `RunEventKind`:

| Upstream event | Extra fields | Mapped kind | Adapter action |
|---|---|---|---|
| `run.started` | `user_message` | `ACCEPTED` | task → `executing` |
| `message.delta` | `delta` | `PROGRESS` | log only (v1) |
| `reasoning.available` | `text` | `PROGRESS` | log only (v1) |
| `tool.started` / `tool.completed` / `tool.failed` | tool info | `PROGRESS` | log only (v1) |
| `approval.request` | approval info | `APPROVAL_REQUIRED` | enqueue `APPROVAL_REQUEST` delivery |
| `approval.responded` | `choice`, `resolved` | `PROGRESS` | log only |
| `run.completed` | `output`, `usage` | `COMPLETED` | extract `output` → enqueue `RESULT` |
| `run.failed` | `error` | `FAILED` | task → `failed`; enqueue `FAILURE_NOTICE` |
| `run.cancelled` | — | `CANCELLED` | confirm task `cancelled`; discard late results |
| (anything else) | — | `UNKNOWN` | log + skip, never fatal |

All events carry `run_id` and `timestamp` (epoch float).

> **⚠ Single-subscriber queue semantics (critical, from source)**: events are
> buffered in an in-memory queue per run. The queue is **destroyed when the
> subscriber disconnects** (`finally: self._run_streams.pop(run_id)`), and
> also when the stream completes. Consequences for the client:
>
> 1. Exactly **one** SSE consumer per run — never two.
> 2. A reconnect after disconnect gets **404** (after a ~1 s server-side
>    grace loop of 20×50 ms used only for subscribe-before-register races).
> 3. Therefore "reconnect with backoff" really means: on any stream drop,
>    **reconcile via `GET /v1/runs/{run_id}`** (which survives independently)
>    and treat a terminal snapshot status as the terminal event. Events
>    emitted while no subscriber was attached are queued and delivered on
>    first attach — but only to the *first* attach.

### `POST /v1/runs/{run_id}/stop`

Cancel. Observed: 200 `{ "run_id": "...", "status": "stopping" }`; the run
then emits `run.cancelled` and the snapshot settles at `status: "cancelled"`,
`last_event: "run.cancelled"`. Adapter marks task `cancelled` optimistically;
a late terminal event for a cancelled task is discarded.

### `POST /v1/runs/{run_id}/approval`

Resolve pending approval. Body (pinned from `_handle_run_approval` source):

```jsonc
{ "choice": "once" }   // one of: "once" | "session" | "always" | "deny"
                       // aliases accepted: approve/approved/allow → once
// optional: "all": true (or "resolve_all": true) — resolve every pending approval
```

Voice mapping: user "yes" → `once`, user "no" → `deny`.

```jsonc
// response
{ "object": "hermes.run.approval_response", "run_id": "...", "choice": "once", "resolved": 1 }
```

Errors: 400 invalid choice, 404 unknown run, 409 `approval_not_active` /
`approval_not_pending`. On success status returns to `running` with
`last_event: "approval.responded"` and an `approval.responded` event on the
stream.

### `POST /v1/chat/completions`  (fallback path only)

OpenAI-compatible; `stream: true` ⇒ SSE `data:` lines with
`choices[0].delta.content`, terminated by `data: [DONE]`. Already implemented in
`voice_satellite/gateway/hermes_client.py` (note: base URL carries `/v1`).
Used when capabilities indicate no runs API. Session headers as above.

## Not consumed (explicitly out of scope)

`/v1/responses*`, `/api/sessions*` (CRUD/fork/per-session chat), `/v1/models`
(informational only), webhook adapter, any non-API-server gateway platform.

## Resolved open questions (research.md §7)

- **OQ-1** (event names/schema): pinned above; the `.plans` doc shapes held,
  with `run.*`/`tool.*`/`approval.*`/`message.delta`/`reasoning.available`
  vocabulary confirmed live + in source.
- **OQ-2** (`X-Hermes-Session-Key` on `/v1/runs`): **yes** — parsed by the
  shared `_parse_session_key_header`, echoed in response headers, and bound to
  the run's approval session; requires API-key auth.
- **OQ-3** (result size limits): no server-side cap on `output` besides the
  10 MB body limit — results can be arbitrarily large (a trivial "pong" run
  already ingests ~20 k input tokens), so the FR-012 speech-condensation path
  is **mandatory**, not nice-to-have.

## Contract test (T101)

`tests/contract/test_hermes_api.py` — skipped unless `HERMES_BASE_URL` is
reachable (env or repo `.env`). Asserts: health 200; capabilities advertise the
runs API + header names; run dispatch → 202 + `run_id`; events stream yields
`run.completed` with `output`/`usage` for a trivial prompt; snapshot agrees
with the stream; stop yields `stopping` → `run.cancelled` → snapshot
`cancelled`; 401 without bearer (runs **and** capabilities); 404 for unknown
runs. Last green: 2026-06-12 against jarlaxle (6 passed, 24 s).
