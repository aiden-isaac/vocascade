# Contract: Hermes Agent API (consumed subset) — re-affirmed for 006

**Status**: **Unchanged from 005.** This feature consumes exactly the async runs
API pinned in
[`../../005-hermes-agent-backend/contracts/hermes-api.md`](../../005-hermes-agent-backend/contracts/hermes-api.md)
(pinned live against jarlaxle, 2026-06-12, T101). No new endpoints, fields, or
auth are required. That document is the authoritative source; this file records
only what 006 changes in **how** the existing contract is consumed.

## What 006 keeps verbatim

- Connection, auth (`Authorization: Bearer`), and the `X-Hermes-Session-Key` /
  `X-Hermes-Session-Id` headers.
- `GET /health`, `GET /v1/capabilities` (probe rule:
  `features.run_submission && features.run_events_sse`).
- `POST /v1/runs` (202 + `run_id`), `GET /v1/runs/{id}` (snapshot),
  `GET /v1/runs/{id}/events` (SSE), `POST /v1/runs/{id}/stop`,
  `POST /v1/runs/{id}/approval`.
- The single-subscriber queue semantics and **reconnect = reconcile via
  `GET /v1/runs/{id}` snapshot** rule (the basis for FR-053 streamed-delivery
  resilience and the "stream drops mid-token" edge case).
- The terminal status vocabulary and the `RunEventKind` mapping.
- The 10-concurrent-run limit and the mandatory speech-condensation path for
  large `output`.

## The one behavioral change in 006

**`message.delta` is now consumed, not log-only.** In 005 the
`message.delta` event (carrying a `delta` text fragment) mapped to `PROGRESS`
and was logged only. In 006 the Hermes stage (`vocascade/waterfall/stages/hermes.py`)
**streams each `message.delta` fragment into TTS as it arrives** so the first
spoken words begin promptly (FR-051, SC-003). The terminal `run.completed`
`output` remains the authoritative full result and the basis for proactive
delivery of late completions (FR-052). No other event mapping changes.

This is the only run-event the proactive `DeliveryCoordinator` did not already
act on; everything else (approval, failure, completion, cancellation) keeps its
005 behavior.

## Phase 0 gate (OQ-1) — verify before building the Hermes stage

Extend `tests/contract/test_hermes_api.py` to assert that
`GET /v1/runs/{id}/events` emits **one or more `message.delta` events with a
non-empty `delta` field before `run.completed`** for a prompt that produces
multi-token output. 005's pinning already observed `message.delta` in the
vocabulary; this assertion confirms the events arrive incrementally (not only a
terminal `run.completed`), which the streamed-delivery model depends on.

**Fallback (recorded if the assertion fails)**: if the live server emits only a
terminal `run.completed`, the Hermes stage degrades to whole-result proactive
delivery — still one async path, no token streaming — and SC-003's first-word
budget is relaxed accordingly. No code branch by query; the difference is purely
how many `message.delta` events the server happens to emit.
