# pluggable-tts

## Why

The only TTS backend is the author's Genie/GPT-SoVITS voice-cloning stack — it
needs an ONNX model dir, reference audio, and a locally running Genie server
that a stranger cannot stand up. A fresh install today silently drops into
degraded text-only mode: a voice assistant with no voice. For a usable beta the
default voice must work with zero setup, which means Piper (CPU-only, fast,
permissively licensed, stock voices) becomes the default and Genie becomes the
custom-voice (Pro) path. The seam already exists — `GenieTTSStage` wraps
`GenieTTSClient`, and the chunking conveyor (`vocascade/tts/chunker.py`, PR
#169) is backend-agnostic — this change formalizes it.

## What Changes

- **TTS Protocol**: one small `Protocol` capturing `GenieTTSClient`'s current
  surface as consumed by the pipeline stage: async-iterated PCM chunks from
  `synthesize(text)`, a declared output `sample_rate`, voice selection at
  construction, plus `stop()` / `close()` / warmup-load. Nothing beyond what
  the stage and conveyor already consume.
- **Backend registry**: a plain dict of names → classes (`piper`, `genie`) in
  `vocascade/tts`. Config selects by name. No plugin discovery, no
  entry-points system.
- **Piper backend (new default)**: a `PiperTTS` client implementing the
  Protocol, running in-process on CPU, shipping stock male/female voices. A
  fresh install with no TTS config gets Piper with a working default voice —
  no server, no model paths, no reference audio.
- **Genie stays registered**: `TTS_BACKEND=genie` selects the existing client
  unchanged — it becomes the custom-voice path, not the default.
- **Backend-agnostic stage**: `GenieTTSStage` generalizes to a `TTSStage` that
  takes any Protocol-conforming client from the registry. The conveyor,
  pipeline order, barge-in/interruption handling, and character effects chain
  are untouched.
- **Sample-rate honesty**: the backend declares its native rate (Piper voices
  are 16/22.05 kHz, Genie is 32 kHz); the stage resamples to the configured
  `audio_out_sample_rate` when they differ, so the wire format never changes.
- **Config**: `TTS_BACKEND` (default `piper`) + `TTS_VOICE` select backend and
  voice. Genie env keys are only consulted when `TTS_BACKEND=genie`. The setup
  GUI voice tab gains the backend/voice fields. **BREAKING**: existing Genie
  installs must set `TTS_BACKEND=genie` — the default flips to Piper.
- **Fillers follow the backend**: `scripts/generate_fillers.py` renders through
  the configured backend so pre-rendered filler audio matches the active voice.

Out of scope: the conveyor itself (shipped in #169), LLM/BYOK config
(de-personalize-byok, on main), packaging/Docker/one-command Piper install
(packaging-deploy — this change assumes `piper-tts` is importable and flags the
dependency seam), harness-as-skill, multi-session.

## Capabilities

### New Capabilities

- `tts-backends`: swappable TTS backends behind one Protocol — registry,
  config selection, Piper as zero-setup default, Genie as registered
  custom-voice backend, degraded text-only mode when the selected backend
  can't initialize.

### Modified Capabilities

<!-- none — no main specs exist yet; TTS behavior is specified fresh above -->

## Impact

- `vocascade/tts/`: new Protocol + registry + `PiperTTS` client;
  `genie_client.py` conforms as-is (or with a thin rename shim).
- `vocascade/pipeline/tts.py`: `GenieTTSStage` → backend-agnostic `TTSStage`
  (construction changes; frame behavior identical).
- `vocascade/config.py` + `.env.example`: `TTS_BACKEND`, `TTS_VOICE`; Genie
  keys become conditional; degraded-mode warning reworded per backend.
- `vocascade/adapter.py`: stage construction goes through the registry;
  startup health report probes the selected backend.
- `vocascade/setup_server.py`: voice tab gains backend + voice selection.
- `scripts/generate_fillers.py`: uses the configured backend.
- **New dependency**: `piper-tts` (ONNX runtime, CPU). Install path/extras
  belong to packaging-deploy; first-run voice-model download (or bundling)
  must be decided there — this change loads voices from a configurable
  models dir with a sensible default cache location.
- Tests: unit tests for the Piper client + registry selection; existing
  Genie tests unchanged.
