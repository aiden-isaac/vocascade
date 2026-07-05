# de-personalize-byok — Tasks

## 1. Config: kill personal defaults, fail fast (D1, D2)

- [x] 1.1 `config.py`: drop defaults for `LLM_BASE_URL`, `LLM_MODEL`,
      `HERMES_BASE_URL`; raise located `ValueError` (naming the key + setup
      GUI) when `LLM_BASE_URL` or `LLM_MODEL` is unset/empty
- [x] 1.2 `gateway/local_llm.py`: remove the `qwen-moe-coder-fast` default
      from the `LocalLLM` constructor (model becomes required)
- [x] 1.3 Update `tests/unit/test_config_adapter.py` (asserts old defaults) +
      add tests: missing `LLM_BASE_URL` raises with located message; empty
      `LLM_API_KEY` accepted; empty `HERMES_BASE_URL` accepted

## 2. Local-only mode (D2, D6)

- [x] 2.1 `adapter.py`: guard `HermesRunClient`/`TaskBroker`/delivery wiring
      on `config.hermes_base_url`; skip cleanly when empty
- [x] 2.2 `waterfall/router.py` `from_config`: drop the `hermes` stage with a
      log line when no Hermes URL is configured
- [x] 2.3 Waterfall exhaustion in local-only mode speaks the can't-help
      notice (router-stage path) instead of ending the turn silently
- [x] 2.4 Tests: server pipeline builds with empty `HERMES_BASE_URL` +
      example config; exhaustion-speaks scenario

## 3. LLM error taxonomy + spoken notices (D3, D4)

- [x] 3.1 `gateway/local_llm.py`: raise `LLMAuthError` (401/403) /
      `LLMUnreachableError` (connect/timeout/5xx), common `LLMError` base
- [x] 3.2 Speak the specific notice on first classified failure per session
      (flag on `SessionState`); subsequent failures keep existing generic
      behavior (smalltalk fallback, classifier fall-through)
- [x] 3.3 Extend `tests/unit/test_degradation.py`: 401 → spoken key notice;
      unreachable → spoken unreachable notice; second failure not repeated;
      new session resets

## 4. Startup probe (D5)

- [x] 4.1 `__main__.py` health report: probe LLM endpoint (short timeout,
      tiny max_tokens) → print OK / AUTH REJECTED / UNREACHABLE; print Hermes
      `probe_capabilities()` verdict when configured; never abort startup
- [x] 4.2 Unit test the probe verdict mapping (mock transport)

## 5. Setup GUI (D7)

- [x] 5.1 `setup_server.py`: reorder `ENV_GROUPS` (Local LLM first), empty
      LLM defaults, provider-example blurbs; mark Hermes group optional
- [x] 5.2 Add `POST /api/test-llm` (and Hermes variant) returning
      ok/auth/unreachable using the 3.1 taxonomy; bounded timeout
- [x] 5.3 `static/setup.html`: test-connection button + verdict display
- [x] 5.4 Test: saved GUI values pass `load_config()` LLM validation

## 6. Docs + examples

- [x] 6.1 `.env.example`: LLM group first and marked required; Hermes marked
      optional (empty default); no personal values
- [x] 6.2 `README.md` + `AGENTS.md`: BYOK setup (cloud key or local endpoint,
      one path), Hermes optional / local-only mode, startup probe mention

## 7. Cleanup sweep (D9)

- [x] 7.1 Scrub `frizzt`/`jarlaxle`/`aiden-isaac` from `scripts/*.py` and
      remaining live tests (`test_hermes_run_client.py`,
      `test_hermes_client.py`, `test_pipeline_roundtrip.py`); leave
      `docs/legacy-specs/**` untouched
- [x] 7.2 Verify the proposal's grep scenario passes: no matches outside
      `docs/legacy-specs/` and git history

## 8. Latency measurement (D8, measure-only)

- [x] 8.1 Add repeat/timing mode to `eval/route_harness` printing p50/p95 for
      the classification path (MEDIUM classifier + smalltalk gate)
- [ ] 8.2 Run against a cloud fast-brain and a local endpoint; record numbers
      in this change's notes — no routing changes
      > DEFERRED: no LLM endpoint available at implementation time (frizzt
      > endpoint no longer resolves; no local model, no cloud key). Tooling
      > verified working (`--time` mode ran; per-stage p50/p95 printed).
      > Run when an endpoint exists:
      > `PYTHONPATH=. .venv/bin/python -m vocascade.eval.route_harness --time 20 "how are you today"`

## 9. Verify

- [x] 9.1 Full test suite green (`PYTHONPATH=. .venv/bin/python -m pytest tests/ -q`)
- [ ] 9.2 End-to-end: fresh `.env` from examples + GUI-entered LLM config →
      server starts, health report verdicts correct, smalltalk answers;
      then break the key and confirm the spoken notice
      > PARTIAL: fail-fast on missing LLM_BASE_URL verified live (located
      > message names the key + setup GUI). Probe verdict mapping, spoken
      > notices, and local-only mode covered by unit tests. The live voice
      > loop (smalltalk answer + broken-key notice) needs a real LLM —
      > blocked on the same missing endpoint as 8.2.
