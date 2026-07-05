# de-personalize-byok

## Why

Vocascade ships with the author's personal infrastructure baked into runtime
defaults: `LLM_BASE_URL` falls back to `https://llm.frizzt.com/v1`
(`config.py:239`, mirrored in `setup_server.py:43`), `HERMES_BASE_URL` falls
back to `http://localhost:8642/v1`, and `LLM_MODEL` defaults to the personal
deployment name `qwen-moe-coder-fast`. A stranger who clones the repo and runs
the server silently points at someone else's endpoint — and when their own LLM
config is wrong, the assistant fails invisibly (utterances fall through the
waterfall or get a generic "trouble responding"). This is the first wall on the
critical path to "a stranger on Linux talks to it in under 30 minutes."

## What Changes

- **BREAKING**: Remove personal runtime defaults. `LLM_BASE_URL` and
  `LLM_MODEL` become required (fail fast at startup with a located message
  pointing at the setup GUI / `.env`, matching the existing fail-fast
  convention). No baked-in endpoint or model name anywhere in source.
- **BREAKING**: `HERMES_BASE_URL` loses its default and becomes **optional**.
  Unset/empty ⇒ the Hermes stage, run client, and task broker are not built;
  the server runs local-only (skills + smalltalk). Today `adapter.py:171`
  builds `HermesRunClient` unconditionally — a stranger does not have a Hermes
  agent, so local-only must be a first-class mode, not a crash.
- **BYOK setup**: the setup GUI collects the LLM connection as the first-run
  essential — base URL + API key + model, one code path whether it's a cloud
  key (OpenRouter, Gemini OpenAI-compat) or a local endpoint (Ollama,
  llama.cpp-server). Add a "test connection" probe that distinguishes
  reachable / auth-rejected / unreachable.
- **Failure UX** (voice apps fail invisibly — extends existing US7
  degradation): startup health report probes the LLM endpoint and prints the
  verdict; at runtime, when the fast-brain LLM is unreachable or the key is
  rejected, the assistant says so out loud ("I can't reach my language model")
  instead of the generic fallback — once per session, not per utterance. When
  an utterance exhausts the waterfall with no Hermes configured, the assistant
  says it can't help with that rather than staying silent.
- **Measure-only**: time the classification-path round-trip (MEDIUM classifier
  + smalltalk gate) against a cloud fast-brain via the existing
  `eval/route_harness`; record numbers, change nothing.
- **Cleanup**: strip personal hostnames/usernames (`frizzt`, `jarlaxle`,
  `aiden-isaac`) from `scripts/*.py` and live tests (several unit tests assert
  the frizzt default, so they change with the default removal regardless).
  `docs/legacy-specs/**` is a read-only historical archive — deferred, left
  as-is.

## Capabilities

### New Capabilities

- `llm-configuration`: how the fast-brain (LLM) and heavy-brain (Hermes)
  endpoints are configured — required vs. optional values, no personal
  defaults, fail-fast behavior, local-only mode when Hermes is absent.
- `setup-gui`: the first-run setup flow — BYOK collection (base URL, key,
  model), connection testing, and what the GUI writes to `.env`.
- `failure-reporting`: spoken and printed failure surfaces — startup endpoint
  probe in the health report, spoken LLM-unreachable notice, spoken
  waterfall-exhausted notice, once-per-session throttling.

### Modified Capabilities

None — `openspec/specs/` has no main specs yet; all three are new.

## Impact

- `vocascade/config.py` — remove defaults at lines 239/241/243; add
  required-value validation for `LLM_BASE_URL`/`LLM_MODEL`.
- `vocascade/adapter.py` — guard Hermes wiring (`:171-180`) on
  `hermes_base_url`; startup LLM probe surface.
- `vocascade/__main__.py` — health report gains LLM/Hermes probe verdicts.
- `vocascade/gateway/local_llm.py` — drop personal model default from the
  constructor; classify auth vs. connection errors for the spoken notice.
- `vocascade/waterfall/router.py`, `vocascade/pipeline/router.py`,
  `vocascade/skills/base_skills/smalltalk.py` — spoken failure notices.
- `vocascade/setup_server.py` + `static/setup.html` — defaults, ordering,
  test-connection endpoint.
- `.env.example`, `README.md` — reflect required/optional split and
  Hermes-optional mode.
- `scripts/check_hermes.py`, `scripts/qwen_pr_summary.py`, tests under
  `tests/unit/` and `tests/integration/` that embed personal values.
- No new dependencies. Out of scope (separate planned changes): pluggable-tts,
  packaging-deploy, harness-as-skill, multi-session.
