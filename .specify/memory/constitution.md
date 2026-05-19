<!--
  SYNC IMPACT REPORT
  ==================
  Version change: 0.0.0 → 1.0.0
  Bump rationale: MAJOR — initial constitution ratification (no prior version)
  Modified principles: N/A (initial version)
  Added sections:
    - Core Principles (6 principles)
    - Hardware & Environment Constraints
    - Development Workflow
    - Governance
  Removed sections: N/A
  Templates requiring updates:
    ✅ .specify/templates/plan-template.md — Constitution Check placeholder
        is generic; aligns with these principles without modification.
    ✅ .specify/templates/spec-template.md — Requirements section uses
        MUST/SHOULD language; compatible, no changes needed.
    ✅ .specify/templates/tasks-template.md — Task categorisation is
        generic (Setup, Foundational, User Story, Polish); accommodates
        observability, error-handling, and config tasks without changes.
    ✅ No .specify/templates/commands/ directory exists; nothing to check.
    ✅ AGENTS.md — references plan; no principle-specific content to update.
  Follow-up TODOs: None.
-->
# OpenClaw Voice Satellite Constitution
## Core Principles
### I. Hardware Agnosticism
All source code, build scripts, and deployment configurations MUST
run without modification on any standard Linux host — from a
developer laptop (x86_64) to a Raspberry Pi (aarch64).
- No audio device indices, hardware serial numbers, or
  platform-specific paths MAY be hardcoded in source files.
- Platform-dependent behaviour (e.g., audio backend selection,
  GPIO access) MUST be abstracted behind an interface that is
  resolved at runtime via configuration or auto-detection.
- CI pipelines MUST validate on at least two architectures
  (x86_64 and aarch64) before a release is tagged.
**Rationale**: The satellite is a public, open-source client.
Contributors run heterogeneous hardware; portability is a
first-class requirement, not an afterthought.
### II. Configuration-Driven Design
Every tuneable parameter — service URLs, model paths, audio
settings, feature flags — MUST be externalised to a central
`.env` file or `config.yaml`.
- No personal paths, credentials, API keys, or audio device
  indices MAY appear in committed source code.
- A `.env.example` (or `config.yaml.example`) MUST be maintained
  in the repository with clearly documented defaults and
  placeholder values for every required variable.
- The application MUST fail fast with a clear, human-readable
  error message when a required configuration value is missing,
  rather than silently falling back to a hardcoded default.
**Rationale**: Open-source contributors MUST be able to clone the
repository and configure it for their own environment without
modifying tracked files.
### III. Modular Architecture
The codebase MUST be organised into discrete, loosely coupled
modules with explicit public interfaces.
- Each module (e.g., STT adapter, TTS adapter, gateway client,
  VAD engine) MUST be independently importable and testable
  without instantiating the full application.
- Cross-module communication MUST occur through well-defined
  interfaces (abstract base classes, protocols, or typed event
  buses) — never via shared mutable global state.
- Adding, replacing, or removing a module (e.g., swapping
  Whisper for a different STT provider) MUST NOT require changes
  to unrelated modules.
**Rationale**: Modularity enables contributors to work on isolated
components, simplifies testing, and allows hardware-specific
adaptations without architectural rewrites.
### IV. Async-First I/O
All network, audio, and inter-process I/O MUST use asynchronous
primitives (`asyncio`, async WebSocket libraries, non-blocking
audio streams).
- The main event loop MUST NOT be blocked by synchronous calls.
  CPU-bound work (e.g., model inference) MUST be offloaded to a
  thread or process pool executor.
- Latency-sensitive paths (microphone capture → STT → LLM →
  TTS → speaker playback) MUST be streamable end-to-end; no
  pipeline stage MAY buffer an entire payload before forwarding.
- All async tasks MUST be structured (via task groups or
  equivalent) so that cancellation propagates cleanly and
  resources are released.
**Rationale**: Voice interaction is real-time. Blocking I/O
introduces perceptible latency and degrades user experience.
### V. Documentation Discipline
Every public module, class, and function MUST carry a docstring
that states its purpose, parameters, return value, and notable
side effects.
- README.md MUST provide a quickstart that enables a new
  contributor to run the satellite within 10 minutes.
- Architecture decisions MUST be recorded in design documents
  (plan.md or equivalent) before implementation begins.
- Inline comments MUST explain *why*, not *what*. Self-evident
  code MUST NOT be cluttered with redundant comments.
**Rationale**: As a public open-source project, the codebase
serves as its own documentation. Undocumented code is
unmaintainable code.
### VI. Resilient Error Handling
The satellite MUST handle transient failures (network drops,
service unavailability, audio device disconnects) gracefully
without crashing.
- All external service calls (gateway, TTS, STT) MUST implement
  retry logic with exponential backoff and configurable timeouts.
- Connection state changes MUST be surfaced to the user via the
  UI (status indicators) and to operators via structured logs —
  never via spoken error stack traces.
- The satellite MUST support a degraded-mode operation where
  partial functionality remains available when a non-critical
  service is unreachable (e.g., TTS offline → text-only
  fallback).
**Rationale**: Edge devices operate on unreliable networks.
A voice assistant that crashes on a dropped connection is
unusable.
## Hardware & Environment Constraints
- **Target Platforms**: Any Linux host with Python 3.11+ and a
  working audio subsystem (ALSA, PulseAudio, PipeWire).
  Raspberry Pi 4/5 (aarch64) is the primary edge target.
- **No GPU Required**: All inference (STT, wakeword) MUST run
  acceptably on CPU. GPU acceleration MAY be used when available
  but MUST NOT be a hard dependency.
- **Network**: The satellite MUST tolerate intermittent
  connectivity. WebSocket reconnection MUST be automatic with
  backoff.
- **Audio**: Audio device selection MUST be driven by
  configuration (device name or ALSA identifier), never by
  hardcoded integer index.
## Development Workflow
- **Branching**: Feature branches follow the naming convention
  enforced by the Spec Kit git extension
  (`###-feature-name`).
- **Specification First**: Every non-trivial feature MUST have a
  spec.md approved before implementation begins.
- **Testing**: Unit tests MUST accompany all new modules.
  Integration tests MUST cover cross-module interactions
  (e.g., STT → LLM router → TTS pipeline).
- **Code Review**: All changes MUST be submitted via pull
  request. Constitution compliance is a mandatory review
  criterion.
- **Commit Hygiene**: Commits MUST be atomic and use
  conventional commit messages
  (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`).
## Governance
This constitution is the supreme governance document for the
OpenClaw Voice Satellite project. All design decisions,
code reviews, and architectural changes MUST be evaluated
against these principles.
- **Amendment Procedure**: Any contributor MAY propose an
  amendment by opening a pull request that modifies this file.
  Amendments MUST include a rationale and MUST update the
  version number per semantic versioning rules below.
- **Versioning Policy**:
  - MAJOR: Removal or backward-incompatible redefinition of a
    core principle.
  - MINOR: Addition of a new principle or material expansion of
    existing guidance.
  - PATCH: Clarifications, typo fixes, non-semantic refinements.
- **Compliance Review**: Every pull request review MUST include a
  constitution compliance check. Violations MUST be resolved
  before merge, or explicitly justified in a Complexity Tracking
  table (see plan template).
**Version**: 1.0.0 | **Ratified**: 2026-05-19 | **Last Amended**: 2026-05-19