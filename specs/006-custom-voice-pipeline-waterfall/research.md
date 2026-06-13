# Research: Custom Voice Pipeline & Confidence Waterfall

**Status**: Verified and Locked (2026-06-13)

Phase 0 decisions. The first three are **blocking gates** — they must be resolved (and OQ-1/OQ-3 verified against reality) before the dependent foundational tasks run. Each entry: Decision / Rationale / Alternatives considered.

## OQ-1 (GATE) — Does `/v1/runs` emit incremental `message.delta` events?

**Decision**: Treat incremental `message.delta` streaming as the delivery mechanism, and **pin it with a contract test in Phase 0** (extend `tests/contract/test_hermes_api.py`) before building `waterfall/stages/hermes.py`. If the live server only emits a terminal `run.completed`, fall back to whole-result proactive delivery (still one path, just no token streaming) and record that here.

**Result (2026-06-13)**: Verified. Live Hermes server (jarlaxle) emits incremental `message.delta` events with non-empty delta values before `run.completed`. Streamed token delivery is confirmed.

**Rationale**: The whole always-async + streamed-delivery model (FR-050/FR-051) depends on the run event stream carrying incremental assistant output, not just lifecycle + terminal events. 005's pinned event vocabulary already lists `message.delta` as a progress event, so the expectation is well-founded — but it must be verified live, not assumed, because the responsiveness budget (SC-003) hinges on it.

**Alternatives considered**: (a) A synchronous chat-completions call for "fast" queries — rejected: reintroduces the stream-vs-dispatch heuristic this feature deliberately removes. (b) Polling `get_run()` for partial output — rejected: higher latency, no token granularity.

## OQ-2 (GATE) — Where does VAD run authoritatively?

**Decision**: The **edge client remains the authoritative VAD** (it already segments speech and sends complete utterances). The server treats inbound audio as pre-segmented. A server-side `silero-vad` is added **only** as an optional fallback for transports/clients that stream raw unsegmented audio, gated behind config; it is not on the default edge path. This keeps the existing `EdgeVADProcessor` behavior (now `pipeline/vad.py`) as the default.

**Rationale**: Double-VAD (edge + server both segmenting) wastes CPU on the constrained edge target and adds latency for no benefit. The current architecture already trusts edge segmentation; preserving that avoids a regression while still allowing a server-VAD path for future clients.

**Alternatives considered**: (a) Move VAD fully server-side — rejected: defeats the edge's bandwidth/latency advantage and the existing design. (b) Always run both — rejected: redundant compute, latency.

## OQ-3 (GATE) — Edge↔server transport authentication

**Decision**: Carry forward an explicit auth posture rather than leaving the endpoint open. Default to **device identity (Ed25519) on the WebSocket transport** (as 004 had) when the edge and server are on an untrusted network; allow a documented **trusted-network mode** (Tailscale as the security boundary) that disables device-identity checks, selected explicitly in config. The transport server (`transport/server.py`) MUST require one of these to be chosen; it MUST NOT default to unauthenticated.

**Rationale**: Replacing the old FastAPI server risks silently dropping 004's Ed25519 device identity and turning a LAN endpoint into an open one (a security regression). Making the choice explicit and config-gated (FR-111) prevents that while accommodating the common Tailscale-only deployment.

**Alternatives considered**: (a) Rely on Tailscale implicitly with no config flag — rejected: accidental open endpoint if deployed off-Tailscale. (b) Mandatory Ed25519 always — rejected: unnecessary friction for single-host (co-located edge+server) setups.

## OQ-4 — `config.yaml` vs `.env` boundary

**Decision**: `config.yaml` holds **structure**: waterfall stage order, thresholds, per-skill settings (enabled/provider/filler), edge/server role assignment, transport auth mode. `.env` holds **secrets and endpoints**: API keys, service URLs, hosts. `config.py` loads both; a `config.yaml.example` is maintained. Missing/malformed required values fail fast with a located message (FR-130/FR-131).

**Rationale**: Constitution II explicitly permits a central `config.yaml`; separating structure (reviewable, shareable) from secrets (never committed) keeps the example file useful and `.env` minimal. Today the repo is `.env`-only via `python-dotenv`; introducing YAML early (US0/Phase 1) with a clear boundary prevents drift.

**Alternatives considered**: (a) Everything in `.env` — rejected: nested/structural config (stage lists, per-skill maps) is awkward and unreviewable in flat env vars. (b) Everything in `config.yaml` including secrets — rejected: secrets must stay out of committed/shareable structure.

## OQ-5 — Medium-stage classifier: prompt generation, output, clamping

**Decision**: At startup, build the classifier prompt by concatenating each enabled skill's `name` + a capped number of `examples` (e.g. ≤5 per skill) into a labeled-classification prompt. The classifier returns a skill label (or "none") plus a confidence; the returned confidence is **clamped into the medium band** (e.g. 0.5–0.8) and "none" maps to no-match. Cap total examples to keep the prompt within a small token budget; expose model and band thresholds in `config.yaml`.

**Rationale**: Auto-generation (FR-013, SC-007) means contributors never hand-edit a prompt; capping examples bounds the medium-stage latency (~100–200ms target) and token cost as the skill set grows. Clamping keeps a misbehaving classifier from leaking into the high band (which would starve keyword skills) or the smalltalk floor.

**Alternatives considered**: (a) One example per skill — rejected: too little signal for disambiguation. (b) Unbounded examples — rejected: latency/token creep (a named risk). (c) Trust the raw model confidence — rejected: models are poorly calibrated; banding is more predictable.

## OQ-6 — Edge↔server transport mechanism

**Decision**: Keep the existing **WebSocket JSON/binary codec** (the ported `RawFrameSerializer` → `transport/serializer.py`): binary frames carry PCM, JSON frames carry control/status. Document a per-hop latency budget in `quickstart.md`. Topology supports co-located (edge+server same host) and split (athrogate↔jarlaxle).

**Rationale**: The WS codec already works and is well-understood; the rewrite is about removing the Pipecat transport wrapper, not changing the wire format. Reusing it minimizes risk and preserves the browser/edge clients.

**Alternatives considered**: (a) gRPC streaming — rejected: heavier dependency, no clear win for a single-session audio stream. (b) Raw TCP — rejected: loses the easy JSON control channel and browser compatibility.

## OQ-7 — Session-summary sync to the memory service

**Decision**: At session end, generate a short natural-language **gist** (not a transcript) of the session with the local LLM, and POST it best-effort to the memory service (Honcho when enabled, else skip). The POST runs after teardown signalling and never blocks it; failures are logged. Reuse the existing context-source/pre-fetch plumbing where applicable.

**Rationale**: Fixes context divergence (FR-090/FR-091) when turns are handled locally without overloading the memory service with full transcripts. Best-effort + non-blocking honors the resilience principle and keeps teardown snappy.

**Alternatives considered**: (a) POST the full transcript every session — rejected: noisy, larger payloads, the memory service only needs the gist. (b) Sync per-turn — rejected: chatty and couples the hot path to the memory service.

## Carried-forward invariants (not open — recorded for traceability)

- Two-brain split is permanent; the local LLM never receives Hermes/skill tool schemas (FR-032).
- All framework-agnostic 005 modules are retained and moved into `vocascade/` (delivery, task_broker, hermes_run_client, transcript_manager, filler_engine, pre_fetch_cache).
- The ported Genie TTS sink must preserve tolerance for a 4–7s first audio chunk (FR-005), which Pipecat's audio-context drain previously provided.
