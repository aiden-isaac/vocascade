# packaging-deploy — Tasks

## 1. Packaging

- [x] 1.1 Write `pyproject.toml`: setuptools backend, `requires-python >= 3.11`,
      base deps from requirements.txt with `piper-tts==1.4.2` pinned and floors
      on the rest, `[edge]` extra (pyaudio, openwakeword, onnxruntime), console
      scripts `vocascade` and `vocascade-edge`, package discovery excluding
      tests/static/scripts/user_skills
- [x] 1.2 Delete `requirements.txt`; switch `.github/workflows/ci.yml` to
      `pip install -e . pytest` and drop `PYTHONPATH=.` where the install makes
      it redundant
- [x] 1.3 Verify in a clean venv: `pip install -e .` → server starts,
      `pip install -e .[edge]` → `vocascade-edge` imports its capture deps;
      `python -m vocascade` / `python -m vocascade.edge` still work
- [x] 1.4 Add `LICENSE` (AGPL-3.0 working choice) + license metadata in
      pyproject and README; flag OQ1 for owner confirmation before merge

## 2. Default wake word

- [x] 2.1 Add wake-word provisioning to the edge client: existing-file path →
      use as-is; bare name → resolve from installed openwakeword or download to
      `~/.vocascade/wakeword/` before the capture loop, with clear logging and
      an actionable failure message (settle OQ2 against the resolved
      openwakeword version)
- [x] 2.2 Change the `WAKE_WORD_MODEL` default to `hey_jarvis` in
      `vocascade/edge/__main__.py` and `.env.example` (same commit as 2.1);
      document both forms in `.env.example`
- [x] 2.3 Unit-test the resolution rule (path passthrough, name provisioning,
      failure message) with the download mocked

## 3. Protocol version + contract doc

- [x] 3.1 Define `PROTOCOL_VERSION = 1` in `vocascade/transport/`; server sends
      `{"type": "hello", "protocol_version": 1}` right after accept in all auth
      modes (`vocascade/adapter.py`)
- [x] 3.2 Edge client consumes hello before auth: equal → proceed, mismatch →
      clear error + disconnect; unit-test both paths with a fake socket
      alongside the existing handshake tests
- [x] 3.3 Write `docs/protocol.md`: hello ordering, both auth modes, binary PCM
      up / base64 JSON down, wakeword/interrupt/status/error, 1008
      single-session rejection, version-bump policy
- [x] 3.4 Confirm the browser client (`static/index.html`) tolerates the hello
      message (ignore or log unknown types)

## 4. Docker host + edge service

- [x] 4.1 Write root `Dockerfile`: python:3.11-slim, non-root user, repo copied
      in (static/ needed), `pip install .`, CMD `vocascade`
- [x] 4.2 Write `docker-compose.yaml`: port 8005, `env_file: .env`, bind-mount
      `config.yaml`, named volume for model caches (TTS_MODELS_DIR + whisper
      cache), host-gateway note for reaching a host-side `LLM_BASE_URL`
- [x] 4.3 Build and run the image; verify an edge client on the same machine
      connects through the published port and models persist across
      `docker compose down && up`
- [x] 4.4 Add `deploy/vocascade-edge.service` systemd user unit + README
      install/enable instructions

## 5. Docs + beta framing

- [x] 5.1 Rewrite README install/run sections: single-box localhost quickstart
      first (with portaudio prerequisite line), then Docker host + native edge
      split topology, then dev install
- [x] 5.2 Add beta-limitations section above the install instructions: single
      satellite/session (1008 on concurrent connect, multi-session = v1.1),
      Linux edge only tested, English-only
- [x] 5.3 Update CLAUDE.md commands section (no more `PYTHONPATH=.` /
      requirements.txt references) and cross-link `docs/protocol.md`

## 6. Verification

- [x] 6.1 Full test suite green in a clean venv installed only via
      `pip install -e .[edge]`
- [x] 6.2 Stranger-path dry run on this machine: fresh clone → quickstart
      commands only → wake word ("hey jarvis") → spoken answer; fix any
      undocumented step it surfaces
- [ ] 6.3 Get OQ1 (license) confirmed by the owner before merge
