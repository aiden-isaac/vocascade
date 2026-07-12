# de-personalize-byok — Design

## Context

`load_config()` (`vocascade/config.py`) reads `.env` via `os.getenv` with
inline defaults; three of those defaults are personal infrastructure
(`llm.frizzt.com`, `localhost:8642`, `qwen-moe-coder-fast`). `adapter.py`
builds `HermesRunClient` + `TaskBroker` unconditionally from
`hermes_base_url`, while the waterfall router already guards its LLM on
`if getattr(config, "llm_base_url", None)` (`waterfall/router.py:200`) and
`adapter.py:200` guards `summary_llm` the same way — so "empty LLM" is
half-tolerated and "empty Hermes" not at all.

Graceful degradation already exists per-stage (US7, `tests/unit/test_degradation.py`):
skill exceptions speak an error, a failed classifier falls through silently,
smalltalk speaks a generic "trouble responding" fallback, and a failed Hermes
dispatch queues a spoken notice. What's missing is *diagnosis*: nothing tells
the user their key is bad or their endpoint URL is wrong — not at startup
(`__main__.py` prints config values, probes nothing) and not in the spoken
fallbacks.

## Goals / Non-Goals

**Goals:**
- Zero personal values in source defaults; fail fast on missing LLM config.
- Local-only mode: server runs fully without a Hermes endpoint.
- A misconfigured LLM is diagnosable in ≤1 utterance: startup probe verdict +
  spoken notice naming the problem class (unreachable vs. key rejected).
- Setup GUI is sufficient to configure BYOK without touching `.env` by hand.
- Cloud fast-brain latency numbers recorded (measure only).

**Non-Goals:**
- No routing re-architecture for cloud latency (measure only).
- No changes to Hermes protocol, TTS, packaging, or the single-session limit.
- No provider-specific adapters — one OpenAI-compatible code path.
- No auth on the setup GUI (stays localhost-only, existing ponytail note).

## Decisions

### D1: `LLM_BASE_URL` + `LLM_MODEL` required, fail fast at load
Missing ⇒ `load_config()` raises `ValueError` with a located message naming
the `.env` key and pointing at `python -m vocascade.setup_server`, matching
the existing required-section convention (`config.py:168`). *Alternative:*
tolerate empty and run keyword-skills-only — rejected: a voice assistant
whose MEDIUM/SMALLTALK brain is absent is broken in a way strangers can't
diagnose; fail-fast with instructions is friendlier than degraded mystery.
`LLM_API_KEY` stays optional (local endpoints often need none).

### D2: `HERMES_BASE_URL` optional ⇒ local-only mode
Empty/unset ⇒ `adapter.py` skips `HermesRunClient`/`TaskBroker` wiring and
the waterfall builds without the hermes stage (config.yaml `stages` listing
`hermes` with no URL just drops it with a startup log line, not an error —
the example config must work out of the box either way). *Alternative:*
require Hermes too — rejected: strangers don't have a Hermes agent; it's the
author's stack. This makes the two-brain design honest: brain #2 is a plugin.

### D3: Error taxonomy lives in `LocalLLM`
`LocalLLM.chat` maps failures to two exception classes: `LLMAuthError`
(HTTP 401/403) and `LLMUnreachableError` (connect/timeout/5xx). Callers that
already catch broadly keep working (both subclass a common `LLMError` /
`Exception`). The spoken notice and the startup probe both key off this
taxonomy — one classification, two surfaces. *Alternative:* classify at each
call site — rejected: three call sites (classifier, smalltalk, summary) would
each reimplement it.

### D4: Spoken notice, once per session
On the first `LLMAuthError`/`LLMUnreachableError` in a session, the
router-stage failure path speaks a specific sentence ("I can't reach my
language model" / "my language model rejected the API key — check your
setup") and sets a per-session flag; later failures in the same session fall
back to the existing generic behavior. Flag lives on `SessionState`.
*Alternative:* speak every time — rejected: an assistant that repeats "I
can't reach my language model" after every utterance is a nag, and the
waterfall may still answer via keyword skills.

### D5: Startup probe = one `chat/completions` call, non-blocking verdict
`__main__.py` health report performs a single short probe of the LLM endpoint
(tiny max_tokens) and prints `LLM endpoint: OK (model …)` /
`AUTH REJECTED` / `UNREACHABLE`; Hermes (when configured) reuses the existing
`probe_capabilities()`. Probe failure warns loudly but does NOT abort startup
— endpoints legitimately come up after the server (e.g. Ollama started
later). *Alternative:* abort on probe failure — rejected: turns a race into a
crash.

### D6: Waterfall exhaustion speaks
When every stage abstains and no hermes stage exists (local-only mode), the
router speaks a fixed "I can't help with that — I don't have an agent
backend configured." instead of ending the turn silently. With Hermes
configured this path is unreachable (hermes is a catch-all), so no behavior
change for existing setups.

### D7: Setup GUI: reorder + test-connection endpoint, no wizard
`ENV_GROUPS` puts **Local LLM first** with empty defaults and blurbs showing
example values for Ollama (`http://localhost:11434/v1`), llama.cpp-server,
OpenRouter, Gemini OpenAI-compat — text only, same code path. Add
`POST /api/test-llm` `{base_url, api_key, model}` that runs the D3-classified
probe and returns ok/auth/unreachable for the GUI to display; same endpoint
shape serves Hermes with its capabilities probe. *Alternative:* multi-step
first-run wizard — rejected: the existing tabbed GUI plus a test button is
enough; a wizard is packaging-deploy-adjacent polish.

### D8: Latency measurement via existing route harness
Add a repeat/timing flag to `eval/route_harness` (or a thin script around it)
that runs N fixture utterances through the classification path against the
configured endpoint and prints p50/p95 per stage. Numbers get recorded in
this change's notes. No routing changes regardless of the result.

### D9: Cleanup scope
`scripts/qwen_pr_summary.py`, `scripts/check_hermes.py`, and every live test
asserting personal defaults get scrubbed (the default-asserting tests break
with D1 anyway). `docs/legacy-specs/**` stays untouched — it's a read-only
historical archive and rewriting history there has no runtime effect.

## Risks / Trade-offs

- [Existing users' `.env` relied on the frizzt default] → BREAKING is
  intentional; the fail-fast message names the exact key to set. Only the
  author is affected today.
- [Startup probe adds latency / hangs on a black-holed endpoint] → short
  timeout (~3s) on the probe only; server starts regardless (D5).
- [`config.yaml.example` lists `hermes` in stages but `.env.example` ships no
  Hermes URL] → D2 explicitly tolerates this combination (log + drop), so the
  out-of-box copy-the-examples path works.
- [Once-per-session flag hides a mid-session recovery] → acceptable; the flag
  resets every session, and log lines still record each failure.
- [Cloud fast-brain p95 may be poor] → explicitly measure-only; the numbers
  feed a future routing decision, not this change.

## Migration Plan

Single PR. Author updates own `.env` (values already present there — only
defaults die). Rollback = revert; no data or schema involved.

## Open Questions

- Exact spoken wording for the two notices (D4) and exhaustion line (D6) —
  pick at implementation time, keep short and TTS-friendly.
