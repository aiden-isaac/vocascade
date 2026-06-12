# Tasks: Hermes Agent Backend Integration

**Input**: Design documents from `/specs/005-hermes-agent-backend/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md,
contracts/hermes-api.md, quickstart.md

**Tests**: Unit tests per module (mocked HTTP/SSE/SFTP); one live contract test
(skipped when no server reachable).

**Organization**: Grouped by user story. Task numbering starts at T101 to avoid
colliding with 004.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1–US6 from spec.md

---

## Phase 0: Contract Pinning (Blocking)

**Purpose**: Resolve every ⚠️ in `contracts/hermes-api.md` against a live
server before building on assumptions.

- [x] T101 Stand up a dev Hermes Agent (topology A or B per quickstart §1–2);
      write `tests/contract/test_hermes_api.py` (skipped unless
      `HERMES_BASE_URL` reachable) asserting: `/health` 200, capabilities
      parse, `POST /v1/runs` → 202+`run_id`, events stream yields a terminal
      event, `GET /v1/runs/{id}` agrees, `/stop` works, 401 without bearer.
- [x] T102 Update `contracts/hermes-api.md` with observed payloads (run-request
      fields, status vocabulary, event names, approval body) — resolve OQ-1,
      OQ-2, OQ-3 from research.md.
- [x] T103 [P] Write `scripts/check_hermes.py` — operator-facing probe used by
      quickstart §2 (health, capabilities, auth check, one trivial round-trip
      run; loud warning when the server is unauthenticated).

**Checkpoint**: contract test green against a live server; contracts doc has no
remaining ⚠️.

---

## Phase 1: Foundational (Blocking)

- [x] T104 Extend `voice_adapter/transcript_manager.py`: add `FAILED` to
      `HermesTaskState`; add `run_id`, `request_text`, `result_text`,
      `session_id`, `delivered` to `HermesTask`; keep `can_cancel()` semantics
      (False once `executing` and Hermes reports non-cancellable — final rule
      per T102 findings).
- [x] T105 [P] Config rework in `voice_adapter/config.py` + `.env.example`:
      add `hermes_session_key`, `hermes_context_source`,
      `hermes_context_poll_interval`, `context_token_budget`,
      `result_speech_budget`, `task_journal_path`; default `honcho_api_url` to
      empty/disabled; **remove** `hermes_sse_url` and `hermes_memory_path`;
      update `tests/unit/test_config_adapter.py`.
- [x] T106 [P] Add `asyncssh` to `requirements.txt`.
- [x] T107 [P] Fix `voice_satellite/gateway/hermes_client.py` URL/headers:
      base URL carries `/v1`; send `X-Hermes-Session-Key` alongside
      `X-Hermes-Session-Id`; keep streaming parse as the fallback path.

**Checkpoint**: config loads, existing 004 tests still pass.

---

## Phase 2: User Story 1 — Voice Query with Proactive Result (P1) 🎯 MVP

### Tests

- [x] T108 [P] [US1] Unit tests `tests/unit/test_hermes_run_client.py` —
      mocked httpx: 202 dispatch, SSE event parse (incl. `UNKNOWN` kinds and
      keepalives), backoff/reconnect, `get_run` reconciliation, capabilities
      probe failure ⇒ fallback flag, auth/session headers present.
- [x] T109 [P] [US1] Unit tests `tests/unit/test_task_broker.py` — dispatch
      creates `pending`→`executing`; completion → result handed to delivery;
      idempotent state machine (duplicate/out-of-order/terminal events);
      dispatch failure → `failed` + failure notice; fallback mode synthesizes
      one completion from a buffered chat stream.
- [x] T110 [P] [US1] Unit tests `tests/unit/test_delivery.py` (MVP subset) —
      idle gate (defer while user/bot speaking), FIFO order, preamble derived
      from `request_text`.

### Implementation

- [x] T111 [US1] Implement `voice_adapter/hermes_run_client.py` per plan §1
      (probe, start_run, stream_events w/ backoff, get_run, stop_run,
      resolve_approval, chat_fallback delegating to `HermesClient`).
- [x] T112 [US1] Implement `voice_adapter/task_broker.py` (registry + per-run
      consumer tasks + fallback mode; no journal yet — that's US6).
- [x] T113 [US1] Implement `voice_adapter/delivery.py` MVP: queue, idle
      detection fed by pipeline frames, injection via
      `AdapterProcessor.inject_text`, preambles.
- [x] T114 [US1] Rewire `voice_adapter/adapter.py`: `handle_query_hermes` →
      `TaskBroker.dispatch()` (delete inline `consume_hermes`); wire
      broker/coordinator into lifespan + frame notifications from
      `AdapterProcessor`/`TeardownInterceptor`; spoken error on dispatch
      failure.

**Checkpoint (MVP)**: quickstart §5 item 1 passes end-to-end by voice.
**STOP and VALIDATE** before continuing.

---

## Phase 3: User Story 2 — Concurrency & Delivery Hardening (P1)

- [ ] T115 [P] [US2] Extend delivery tests: buffering mid-utterance, two
      queued results spoken sequentially with pause, barge-in during delivery
      ⇒ stop + `[interrupted]` commit + no re-queue, condensation path
      triggered over `RESULT_SPEECH_BUDGET`.
- [ ] T116 [US2] Implement multi-task hardening in `task_broker.py` +
      `delivery.py`: N concurrent consumers, completion-order queueing,
      cancelled-task result discard keyed by `run_id`.
- [ ] T117 [US2] Implement result condensation via one local-LLM call (full
      text → transcript turn, condensed → speech), behind
      `RESULT_SPEECH_BUDGET`.
- [ ] T118 [US2] 20-dispatch soak script `scratch/soak_dispatch.py` (manual)
      validating SC-003/SC-004 (no mix-ups, local latency unchanged).

**Checkpoint**: quickstart §5 item 2 passes; SC-004 verified.

---

## Phase 4: User Story 3 — Fresh-Machine Bootstrap (P1)

- [ ] T119 [P] [US3] Write `scripts/setup_hermes.sh`: official installer →
      api_server enablement → `API_SERVER_KEY` generation → gateway start
      hints (systemd snippet); idempotent re-runs.
- [ ] T120 [P] [US3] Finalize `quickstart.md` against a real fresh-machine run
      (container or spare box): time it (SC-005 ≤ 30 min), fix drift.
- [ ] T121 [US3] Startup validation in adapter lifespan: capabilities probe
      result logged; loud warning when server is unauthenticated; spoken vs
      logged distinction between unreachable (connection) and unauthorized
      (401) per spec edge cases.
- [ ] T122 [US3] Verify memory persistence scenario (quickstart §5 item 3) on
      built-in memory AND document the Honcho opt-in path (no code changes —
      doc verification only).

**Checkpoint**: a fresh machine reaches MVP with zero external services.

---

## Phase 5: User Story 4 — Context Hydration (P2)

### Tests

- [ ] T123 [P] [US4] Unit tests `tests/unit/test_context_source.py` — URI
      parsing (`file://`/`ssh://`/`none`/garbage), `LocalFileSource` change
      detection (tmpdir + watchdog), `SshFileSource` with mocked asyncssh
      (stat-guard skips unchanged, failure ⇒ stale + health flag).
- [ ] T124 [P] [US4] Rewrite `tests/unit/test_pre_fetch_cache.py` for the real
      cache: merge, `is_warm` gate + 10 s timeout, `build_prompt_block` budget
      truncation at section boundaries and priority order, missing files ⇒
      empty block.

### Implementation

- [ ] T125 [US4] Implement `voice_adapter/context_source.py`
      (`ContextSource` ABC, `LocalFileSource`, `SshFileSource`, `NullSource`,
      `parse_context_source`).
- [ ] T126 [US4] Reimplement `voice_adapter/pre_fetch_cache.py` over sources:
      snapshot merge, lock-guarded `get_context()`, `build_prompt_block()`,
      `source_health`.
- [ ] T127 [US4] [P] Implement optional `HonchoSource` gated on
      `HONCHO_API_URL` (FR-020).
- [ ] T128 [US4] Wire into `adapter.py`: per-turn system prompt = static
      instructions + prompt block (+ task tags via
      `get_context_for_prompt()`); warm gate before first utterance (10 s
      timeout); confirm tool list still excludes all Hermes schemas (FR-018).

**Checkpoint**: quickstart §5 item 4 passes; SC-006/SC-007 measured.

---

## Phase 6: User Story 5 — Status, Cancellation & Approvals (P2)

- [ ] T129 [P] [US5] Unit tests: cancel tool honors `can_cancel()`; stop call
      issued; late result for cancelled run discarded; approval event →
      `APPROVAL_REQUEST` delivery → decision relayed.
- [ ] T130 [US5] Add `cancel_task` tool schema + handler in `adapter.py`
      (resolves "that"/most-recent task from tags; refusal message when
      non-cancellable).
- [ ] T131 [US5] System-prompt guidance so the local LLM answers status
      questions from `[TASK:… STATE:…]` tags without dispatching.
- [ ] T132 [US5] Approval flow: `APPROVAL_REQUIRED` event → spoken request via
      delivery queue → capture next user yes/no (bounded window) →
      `resolve_approval`; un-answered approvals re-announced next session.

**Checkpoint**: quickstart §5 item 5 passes.

---

## Phase 7: User Story 6 — Persistence Across Sessions/Restarts (P3)

- [ ] T133 [P] [US6] Unit tests: journal write-on-transition + atomicity,
      corrupt journal ⇒ archive + fresh, restore re-subscribes executing runs
      and re-queues undelivered results, session-end retention + next-session
      backlog announcement.
- [ ] T134 [US6] Implement `TaskJournal` in `task_broker.py`
      (`TASK_JOURNAL_PATH`, atomic replace, schema version).
- [ ] T135 [US6] Restart recovery in lifespan: restore → re-subscribe →
      reconcile via `get_run`.
- [ ] T136 [US6] Session-boundary handling in `delivery.py`: retain
      undelivered results on teardown; announce backlog after wakeword ack on
      next session (`BACKLOG_ANNOUNCEMENT` kind).

**Checkpoint**: SC-008 passes (restart with run in flight still delivers).

---

## Phase 8: Polish & Cross-Cutting

- [ ] T137 [P] Update `README.md` + `AGENTS.md`: new modules, topology
      diagram, config reference.
- [ ] T138 [P] Structured logging for new modules
      (`voice_adapter.task_broker`, `.delivery`, `.context_source`,
      `.hermes_run_client`).
- [ ] T139 Full suite: `PYTHONPATH=. .venv/bin/python -m pytest tests/ -q` —
      all 004 tests still green.
- [ ] T140 24 h soak (SC-009): no orphaned SSE connections, bounded
      registry/queue; measure SC-001/SC-002/SC-003 and record results here.
- [ ] T141 [P] Mark superseded 004 items: annotate US3/US6 stubs in
      `specs/004-pipecat-voice-adapter/tasks.md` as superseded by 005
      (`pipecat_bridge.py` → `task_broker.py`+`delivery.py`; `PreFetchCache`
      stub → real implementation).

---

## Dependencies & Execution Order

```
Phase 0 (contract) ─► Phase 1 (foundational)
                          ▼
                     Phase 2 (US1) 🎯 MVP ── STOP & VALIDATE
                      ▼           ▼
              Phase 3 (US2)   Phase 4 (US3)     ← parallel after MVP
                      ▼
              Phase 5 (US4)  ← independent of 3/4, needs Phase 1 only;
                      ▼         scheduled after US2/US3 by priority
              Phase 6 (US5) ─► Phase 7 (US6) ─► Phase 8 (Polish)
```

- Phase 0 blocks everything (contract truths shape the client).
- US4 (context) technically depends only on Phase 1 — it can be pulled earlier
  if context quality blocks MVP testing.
- US5 depends on US1 (broker) and benefits from US4's tags; US6 depends on
  US1+US2 delivery semantics.

## Summary

- **Total tasks**: 41 (T101–T141)
- **MVP scope**: Phase 0 + Phase 1 + Phase 2 (14 tasks) → quickstart §5 item 1
- **Parallel opportunities**: 14 tasks marked [P]
- **New modules**: `hermes_run_client.py`, `task_broker.py`, `delivery.py`,
  `context_source.py`; reworked: `pre_fetch_cache.py`, `config.py`,
  `adapter.py`; new scripts: `setup_hermes.sh`, `check_hermes.py`
- **Removed**: `HERMES_SSE_URL`/`HERMES_MEMORY_PATH` config, planned
  `pipecat_bridge.py`, planned `offline_handler.py` interactions (US7/004
  unchanged, untouched)

## Notes

- Commit after each task or logical group; stop at any checkpoint to validate
  the story independently.
- Existing `voice_satellite/` and 004 adapter tests must continue passing.
- Do NOT mark tasks `[x]` ahead of verified implementation (lesson from 004's
  reality-check section).
