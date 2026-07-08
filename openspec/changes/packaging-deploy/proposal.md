# packaging-deploy

## Why

Vocascade works on main but only for its author: it runs via `PYTHONPATH=.`, has no
`pyproject.toml`, its edge client lazily imports undeclared dependencies (pyaudio,
openwakeword, onnxruntime), and the default wake-word model (`eden_wakeword.onnx`)
is personal and gitignored — a fresh clone cannot wake the assistant at all. This is
the last critical-path change before beta: after it, a stranger on Linux goes from
install to talking without help from the author.

## What Changes

- Add `pyproject.toml`: the `vocascade` package becomes pip-installable with console
  entry points `vocascade` (host server) and `vocascade-edge` (edge client), and an
  `[edge]` extra pulling pyaudio, openwakeword, and onnxruntime.
- Declare every currently-undeclared runtime dependency; pin `piper-tts` in
  `pyproject.toml` and remove the stopgap line from `requirements.txt` (which becomes
  a thin shim or is retired in favor of `pip install -e .`).
- **BREAKING**: remove the personal `eden_wakeword.onnx` default AND, in the same
  change, ship a permissive default wake-word model (openwakeword pre-trained,
  Apache-2.0, auto-downloaded on first run) so there is never a window where a fresh
  clone has no wake word. Existing installs with `WAKE_WORD_MODEL` set are unaffected.
- Docker as the flagship host install: the host touches no audio hardware, so a
  Dockerfile + compose file with a volume for models/config. The edge client is
  explicitly NOT containerized — it installs natively via `pip install vocascade[edge]`
  with a documented systemd user unit.
- Document the edge↔host WebSocket protocol as a public contract
  (`docs/protocol.md`) and add a protocol version to the handshake so future clients
  (Android, ESP32) build against a stable contract.
- Add a LICENSE file (AGPL-3.0 working choice — confirm before merge; see design).
- Beta framing in README/docs, stated honestly: single satellite only (the `/ws`
  endpoint is single-session by design for now), Linux edge tested, English-only.
  Single-box (both roles on one machine over localhost) documented as the default
  quickstart.

Out of scope: multi-session/multi-satellite (v1.1), harness-as-skill, cloud/licensing
backend, mobile clients, flashable Pi image.

## Capabilities

### New Capabilities

- `packaging`: pip-installable distribution — `pyproject.toml` metadata, console
  entry points, complete dependency declaration, the `[edge]` extra, and the LICENSE.
- `deployment`: how strangers run it — Docker flagship host install with persistent
  volume, native edge install with systemd unit, single-box localhost quickstart,
  and honest beta-limitations documentation (including the single-session `/ws` limit).
- `wake-word-default`: out-of-the-box wake word — permissive default model
  provisioning (auto-download on first run), paired removal of the personal default,
  and `WAKE_WORD_MODEL` override behavior.
- `ws-protocol`: the edge↔host WebSocket contract as a documented public interface
  with an explicit protocol version exchanged in the handshake.

### Modified Capabilities

None. `tts-backends` requirements are untouched: piper-tts moving from
`requirements.txt` to `pyproject.toml` base deps is an implementation detail that
preserves its "fresh install speaks" scenario.

## Impact

- **New files**: `pyproject.toml`, `LICENSE`, `Dockerfile`, `docker-compose.yaml`,
  `docs/protocol.md`, systemd unit example, default wake-word provisioning code.
- **Modified**: `requirements.txt` (retire/shim), `vocascade/edge/__main__.py`
  (wake-word default + provisioning, handshake version), `vocascade/adapter.py`
  (protocol version in handshake), `.env.example` (WAKE_WORD_MODEL default),
  `README.md` (quickstart rewrite, beta framing).
- **Dependencies**: newly declared — pyaudio, openwakeword, onnxruntime (all under
  the `[edge]` extra); everything in `requirements.txt` moves to `pyproject.toml`.
- **Compatibility**: existing `.env` files keep working; `python -m vocascade` and
  `python -m vocascade.edge` remain valid alongside the new console scripts. The
  protocol version is additive — current clients ignore unknown handshake fields.
