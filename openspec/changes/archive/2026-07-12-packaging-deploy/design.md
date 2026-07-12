# packaging-deploy — Design

## Context

Vocascade currently runs only from a checkout with `PYTHONPATH=.`: no
`pyproject.toml`, no LICENSE, no container, and the edge client's capture deps
(pyaudio, openwakeword, onnxruntime) are lazily imported but never declared
anywhere installable. `requirements.txt` carries a stopgap `piper-tts` line whose
comment explicitly defers pin/extras strategy to this change. The default wake-word
model is the author's personal `eden_wakeword.onnx` (gitignored), so a fresh clone
has no wake word. The `/ws` endpoint is deliberately single-session
(`_session_lock` in `vocascade/adapter.py`).

Constraints that shape the design:

- `STATIC_DIR = Path(__file__).resolve().parent.parent / "static"`
  ([adapter.py:268](../../..//vocascade/adapter.py)) — static assets live at the
  **repo root**, outside the package. A non-editable wheel would not carry them.
- The host never touches audio hardware (edge owns mic/speaker), so it
  containerizes cleanly; the edge client needs `/dev/snd`/PipeWire and does not.
- `.github/workflows/ci.yml` and `README.md` install from `requirements.txt`.
- Both halves ship from the same repo; there is no deployed third-party client
  fleet yet, so the handshake can gain a version field now, cheaply.

## Goals / Non-Goals

**Goals:**

- A stranger on Linux goes from fresh clone to talking assistant without help:
  Docker host + native edge, or single-box localhost quickstart.
- `pip install .` / `pip install .[edge]` works; console scripts `vocascade` and
  `vocascade-edge` replace `PYTHONPATH=. python -m …` invocations.
- Every runtime dependency declared; piper-tts pinned; stopgap line removed.
- Fresh clone wakes on a permissive default wake word with zero manual model steps.
- The WS protocol is a documented public contract with a version in the handshake.
- Honest beta docs: single satellite, Linux edge tested, English-only.

**Non-Goals:**

- PyPI publication (package is made ready; publishing is a later decision).
- Multi-session `/ws` (v1.1), edge containerization, GPU images, Pi image,
  Windows/macOS edge support, protocol negotiation beyond a version check.

## Decisions

### D1: setuptools + flat layout, entry points, `[edge]` extra

`pyproject.toml` with the setuptools backend (boring, zero migration — the package
already imports as `vocascade`). `requires-python = ">=3.11"`. Console scripts:
`vocascade = vocascade.__main__:main`, `vocascade-edge = vocascade.edge.__main__:main`
(both `main()` functions already exist). Base dependencies = current
`requirements.txt` set with `piper-tts==1.4.2` pinned exactly (voice-model format
compatibility); everything else gets floor constraints (`>=` current venv versions).
`[project.optional-dependencies] edge = ["pyaudio>=0.2.14", "openwakeword>=0.4.0",
"onnxruntime>=1.24"]` — matches the lazy imports in `vocascade/edge/__main__.py`.
Package discovery excludes `tests`, `static`, `scripts`, `user_skills`.

*Alternative rejected:* hatchling/poetry — no benefit over setuptools here, one more
tool for contributors to learn.

### D2: install modes scoped around the repo-root `static/` constraint

`static/` stays at the repo root; we do **not** move it into the package in this
change. Consequences, made explicit in docs:

- **Host**: runs from a checkout — either the Docker image (which copies the repo
  in) or `pip install -e .` in a clone. This is how it works today; the flagship
  path (Docker) is unaffected by the wheel limitation.
- **Edge**: after D4 removes the `static/wakeword/` default, the edge client no
  longer reads anything under `static/`, so `pip install vocascade[edge]` works
  from a wheel/sdist with no checkout.

*Alternative rejected:* moving `static/` into the package (importlib.resources)
— the right move when PyPI publication happens, but it touches adapter,
setup_server, filler engine, and scripts for zero beta benefit. Deferred; noted
as the prerequisite for a host wheel.

### D3: `requirements.txt` retired

Deleted in favor of `pip install -e .`. CI (`ci.yml`) switches to
`pip install -e . pytest` (base deps only — tests never needed the edge extra,
since CI passed without those packages). README install steps updated. Keeping a
shim file that just says `-e .` invites drift; a missing file fails loudly.

### D4: default wake word — openwakeword pre-trained, resolved by name

`WAKE_WORD_MODEL` default changes from `static/wakeword/eden_wakeword.onnx` to the
bare name `hey_jarvis` (openwakeword's Apache-2.0 pre-trained model). Resolution
rule in the edge client: a value that is an existing file path is used as-is
(existing installs unaffected); otherwise it is treated as an openwakeword
pre-trained model name and provisioned automatically (bundled with the installed
openwakeword version or downloaded to `~/.vocascade/wakeword/` on first run,
whichever the installed openwakeword supports). Provisioning happens once, before
the capture loop starts, with a clear log line. `.env.example` updated to show
both forms. Removal of the personal default and arrival of the working default
land in the same commit — no window without a wake word.

*Alternative rejected:* vendoring an .onnx in git — binary blob in the repo, and
openwakeword already distributes exactly this artifact under a permissive license.

### D5: protocol version — server `hello` first, exact match

`PROTOCOL_VERSION = 1` defined once in `vocascade/transport/` (single source for
server and edge). Immediately after `accept()` (and before the auth gate) the
server sends `{"type": "hello", "protocol_version": 1}` in **all** auth modes. The
edge client reads it first: version equal → proceed (then auth as today); version
different → log a clear "update vocascade/vocascade-edge" error and disconnect.
Unknown-type JSON remains ignored by both sides, so the browser client
(`static/index.html`) needs no change beyond optionally logging it. The full wire
contract (hello, auth_challenge/auth_response/auth_ok, binary PCM in, base64
audio JSON out, wakeword/interrupt/status/error, single-session 1008 rejection)
is written down in `docs/protocol.md` as the public contract for future clients.

*Alternative rejected:* min/max version negotiation — one integer and exact match
is enough while every client ships from this repo; negotiation can be added
compatibly later because `hello` is extensible.

### D6: Docker is the flagship host install; edge is native

Root `Dockerfile` (python:3.11-slim, non-root user, `pip install .`, repo copied
in for `static/`) and `docker-compose.yaml` (port 8005, `env_file: .env`, named
volume mounted for model caches — `TTS_MODELS_DIR`, whisper download cache — plus
bind-mounted `config.yaml`). CPU-only; works on x86_64 and aarch64 (all base deps
publish manylinux aarch64 wheels). The edge client is **not** containerized:
mic/speaker passthrough is a support nightmare. Instead: `pip install
vocascade[edge]` natively plus a documented systemd **user** unit shipped at
`deploy/vocascade-edge.service` (user unit, not system — PipeWire/Pulse live in
the user session).

### D7: docs — quickstart, beta framing, license

README restructured around three paths, in order: single-box localhost quickstart
(default), Docker host + native edge (split topology), dev install. A "Beta
limitations" section states plainly: one satellite per host (single-session `/ws`,
concurrent connects rejected with 1008 — multi-session is v1.1), Linux edge only
tested, English-only. `LICENSE` = AGPL-3.0 as the working choice (protects the
paid tier from rehosted forks; MIT maximizes funnel) — **must be confirmed by the
author before merge** (OQ1).

## Risks / Trade-offs

- [Wake-word quality: "hey jarvis" false-accept/reject profile differs from the
  tuned personal model] → threshold stays configurable (`WAKE_WORD_THRESHOLD`),
  docs show how to point `WAKE_WORD_MODEL` at any custom .onnx.
- [First-run wake-word download needs network] → failure is caught and surfaced
  as a clear "no wake word available" error with the manual-download command; the
  path-form of `WAKE_WORD_MODEL` works fully offline.
- [pyaudio needs the portaudio C library; pip install fails cryptically without
  it] → README lists the one-line apt/dnf/pacman prerequisite next to the
  `[edge]` install command.
- [`hello` before `auth_challenge` breaks an old edge client against a new server
  in device-identity mode] → accepted: both halves ship from this repo, beta docs
  say to update both together; the protocol doc makes ordering explicit from v1.
- [Docker image can't reach a host-side LLM endpoint via `localhost`] → compose
  file documents `host.docker.internal` / host-gateway mapping for `LLM_BASE_URL`.
- [Deleting requirements.txt breaks muscle memory / external scripts] → README
  and CI updated in the same commit; `pip install -e .` failure message is obvious.

## Migration Plan

1. Land packaging + deps + wake-word default + protocol version + docs in one
   change (single PR); CI switches with it.
2. Existing installs: `.env` values keep working; `WAKE_WORD_MODEL` paths are
   honored; `python -m vocascade` / `python -m vocascade.edge` keep working
   alongside the new console scripts. Update host and edge together (D5).
3. Rollback: revert the PR — no data or config format changes anywhere.

## Open Questions

- **OQ1 (blocking merge, not implementation):** license — AGPL-3.0 working
  placeholder; author must confirm AGPL vs MIT vs BSL before the PR merges.
- **OQ2:** exact openwakeword pretrained-model provisioning API differs across
  versions (bundled resources in 0.4.x vs `download_models()` in newer) — settle
  at implementation against the version the `[edge]` extra actually resolves.
