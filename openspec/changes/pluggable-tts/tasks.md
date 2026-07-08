# pluggable-tts — Tasks

## 1. Protocol + registry (pure refactor, genie only — deployable checkpoint)

- [x] 1.1 Add `vocascade/tts/protocol.py`: `TTSBackend` Protocol
      (`sample_rate`, `degraded_mode`, `start()`, `synthesize()`, `stop()`,
      `close()`) and `REGISTRY: dict[str, Callable[[AdapterConfig], TTSBackend]]`
      with a `genie` factory (import inside the factory).
- [x] 1.2 Make `GenieTTSClient` conform: add `sample_rate = 32000` attribute
      and `start()` alias for `load_character()`; body untouched.
- [x] 1.3 Rename `GenieTTSStage` → `TTSStage` taking a constructed backend
      client instead of building `GenieTTSClient` internally; keep warmup
      (start + throwaway synth) and interruption handling in the stage.
- [x] 1.4 Wire `adapter.py` to build the client via the registry and pass it
      to `TTSStage`; update imports/tests referencing `GenieTTSStage`.
- [x] 1.5 Run full test suite — green with zero behavior change
      (`PYTHONPATH=. .venv/bin/python -m pytest tests/ -q`).

## 2. Piper backend

- [x] 2.1 Add `vocascade/tts/piper_client.py`: `PiperTTS` conforming to the
      Protocol; blocking synthesis via `asyncio.to_thread` + queue; mono s16le
      even-length chunks; `stop()` flag checked between chunks; native
      `sample_rate` read from the loaded voice.
- [x] 2.2 Voice resolution: `TTS_MODELS_DIR` (default
      `~/.local/share/vocascade/piper/`), friendly `female`/`male` aliases
      mapped to two stock en_US voice ids (pick by listening test); default
      voice `female`.
- [x] 2.3 `start()`: if voice files absent, one bounded best-effort download
      of the stock voice via piper's download helper; on failure enter
      degraded mode with a located message (voice, dir, install command).
- [x] 2.4 Register the `piper` factory; verify `TTS_BACKEND=genie` never
      imports the `piper` package.
- [x] 2.5 Unit tests: fake-backend stage test (protocol conformance), piper
      chunk contract with a mocked voice, degraded-on-missing-voice message.

## 3. Sample-rate normalization

- [x] 3.1 Add a small numpy linear resampler in `vocascade/audio/`
      (s16 mono, rate→rate) with a self-check.
- [x] 3.2 `TTSStage` resamples chunks to `audio_out_sample_rate` when the
      backend's native rate differs, before effects/gain; unit test
      22050→32000 frame rate stamping.

## 4. Config, selection, and defaults

- [x] 4.1 `config.py`: add `TTS_BACKEND` (default `piper`), `TTS_VOICE`,
      `TTS_MODELS_DIR`; read `GENIE_*` keys only when backend is `genie`;
      make the incomplete-TTS degraded warning genie-specific.
- [x] 4.2 Unknown `TTS_BACKEND` fails startup fast with a located message
      listing registered names; unit test.
- [x] 4.3 Startup health report probes the selected backend (genie → HTTP
      ping, piper → voice load), 3s bounded, never blocks startup.
- [x] 4.4 Update `.env.example` with the new keys and a
      `TTS_BACKEND=genie` migration note for existing voice-clone installs.

## 5. Setup GUI + fillers

- [x] 5.1 `setup_server.py` voice tab: backend dropdown (from registry) +
      `TTS_VOICE` field; label Genie fields as genie-only; persists to `.env`.
- [x] 5.2 `scripts/generate_fillers.py`: build the client via registry +
      config instead of hardcoding `GenieTTSClient`.
- [x] 5.3 Regenerate `static/fillers/` with the Piper default voice.

## 6. Verify + docs

- [x] 6.1 Full test suite green; end-to-end check: fresh `.env` with no TTS
      keys speaks via Piper; `TTS_BACKEND=genie` path unchanged.
- [x] 6.2 Update `AGENTS.md`/README TTS sections (backend selection, default
      voice, degraded-mode wording); flag `piper-tts` dependency pin +
      voice bundling to packaging-deploy.
