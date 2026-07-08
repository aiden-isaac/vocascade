# pluggable-tts — Design

## Context

TTS today is one hardwired path: `GenieTTSStage` (`vocascade/pipeline/tts.py`)
constructs a `GenieTTSClient` (`vocascade/tts/genie_client.py`) and consumes
exactly this surface:

- `synthesize(text) -> AsyncIterator[bytes]` — mono s16le PCM chunks
- `load_character()` (via `stage.start()`/`warmup()`), `stop()`, `close()`
- `degraded_mode: bool` — set on failure, checked before warmup/synthesis
- sample rate is *not* on the client — the stage is told `sample_rate=32000`
  (`config.audio_out_sample_rate`) and stamps it on every `AudioFrame`

Everything upstream is already backend-agnostic: the chunking conveyor
(`vocascade/tts/chunker.py`, PR #169) hands the stage sentence-sized
`TextFrame`s; barge-in, character effects, and volume are applied in the stage
after the PCM comes back. Config (`config.py:64–70, 201–218`) and the setup
GUI voice tab speak only Genie env keys, and missing voice-clone keys put the
whole system in text-only degraded mode — which is what every fresh install
hits today.

## Goals / Non-Goals

**Goals:**

- One `TTSBackend` Protocol capturing the surface above — nothing more.
- Registry = plain dict; `TTS_BACKEND` env var picks by name; `piper` is the
  default so a stranger gets a working voice with zero TTS config.
- Genie stays registered and byte-identical in behavior when selected.
- Wire format unchanged: frames leave the stage at `audio_out_sample_rate`
  regardless of backend.

**Non-Goals:**

- Plugin discovery / entry points — the registry is a dict in source.
- Rebuilding the conveyor, pipeline order, or barge-in handling.
- Piper install/packaging (pyproject extras, Docker, voice bundling) — owned
  by packaging-deploy; this change assumes `piper` is importable.
- Per-utterance voice switching; voice is fixed at construction like today.

## Decisions

### D1. Protocol lives in `vocascade/tts/protocol.py`, registry beside it

```python
class TTSBackend(Protocol):
    sample_rate: int          # native PCM rate of synthesize() output
    degraded_mode: bool
    async def start(self) -> None: ...      # load voice; degrade on failure
    def synthesize(self, text: str, *, session: str = "") -> AsyncIterator[bytes]: ...
    async def stop(self) -> None: ...
    async def close(self) -> None: ...
```

`REGISTRY: dict[str, Callable[[AdapterConfig], TTSBackend]]` — name → factory.
Factories import their backend module *inside* the function so an uninstalled
`piper` package only fails when `piper` is actually selected, and selecting
`genie` never imports piper. Alternative considered: dict of name → class with
top-level imports — rejected because it makes `piper-tts` a hard import for
Genie-only installs. `GenieTTSClient` conforms by adding the two attributes
(`sample_rate = 32000`) and aliasing `load_character` → `start`; its body is
untouched. Warmup stays in the stage (it is already just `start()` + one
throwaway `synthesize()`), so backends don't each reimplement it.

### D2. Piper runs in-process, off the event loop

`vocascade/tts/piper_client.py` wraps the `piper` Python API
(`PiperVoice.load(<voice>.onnx)`, per-sentence chunk synthesis). Piper
synthesis is blocking CPU work, so the client runs each segment's synthesis in
one `asyncio.to_thread` call and then yields the resulting per-sentence PCM
chunks — same chunk contract as Genie (mono s16le, even-length). The conveyor
already caps segments at ~220 chars (~1–2 sentences), so per-call latency
stays sub-second on CPU; a queue-streaming worker is the upgrade path if
first-chunk latency ever matters (ponytail-marked in source). `stop()` sets a
flag that drops remaining chunks; `close()` is a no-op (no HTTP session).
Loaded voices are cached at module level so per-session client construction
doesn't reload the ONNX model.

Voices are `.onnx` + `.json` files in a models dir (`TTS_MODELS_DIR`, default
`~/.local/share/vocascade/piper/`). Friendly names map to stock voices —
`female` and `male` aliases over two concrete Piper voice ids (picked at
implementation; `en_US-lessac-medium` / `en_US-ryan-medium` are the
candidates). Default `TTS_VOICE=female`.

### D3. Missing Piper voice: best-effort auto-download, then degrade

Zero-setup is the goal, but voice acquisition is packaging-deploy's seam. The
compromise: on `start()`, if the voice files are absent, attempt one download
of the stock voice into `TTS_MODELS_DIR` via piper's own download helper
(~60 MB, best-effort, bounded timeout); on any failure, enter degraded
text-only mode with a located message naming the voice, the dir, and the
install command. Alternative — hard-fail at startup — rejected: TTS is
degradable by principle, and the LLM (which *is* required) already
demonstrates the fail-fast path. Packaging-deploy can pre-bundle voices to
remove the first-run network dependency; nothing here changes if it does.

### D4. Stage resamples to the wire rate; `AudioFrame` rate never varies

`GenieTTSStage` becomes `TTSStage` taking a constructed `TTSBackend` (adapter
builds it via the registry). If `client.sample_rate != audio_out_sample_rate`,
the stage linear-resamples each chunk with numpy (already a dependency via the
DSP effects chain) before effects/gain. Alternative — stamp the backend's
native rate on `AudioFrame` and let clients adapt — rejected: fillers and the
router already push PCM at the configured wire rate, and the edge/browser
players assume one playback rate; mixing per-frame rates moves the problem to
every client. Linear interpolation is fine for speech at 22.05→32 kHz
(upsampling); the resampler is one small function in `vocascade/audio/` with a
known quality ceiling (upgrade to soxr/scipy if it ever audibly matters).

### D5. Config: two new keys, Genie keys go conditional

- `TTS_BACKEND` (default `piper`), `TTS_VOICE` (default per backend:
  `female` for piper, `GENIE_CHARACTER_NAME` keeps working for genie),
  `TTS_MODELS_DIR` (optional).
- `GENIE_*` keys are read only when `TTS_BACKEND=genie`; the "TTS
  Configuration incomplete → degraded" warning becomes genie-specific. A
  fresh `.env` with no TTS keys at all now yields a speaking system.
- **BREAKING**: existing Genie installs must add `TTS_BACKEND=genie`. No
  auto-detection from the presence of `GENIE_ONNX_MODEL_DIR` — implicit
  backend selection is exactly the kind of magic that bites at 3am. One
  documented env var is the migration.
- Setup GUI voice tab gains a backend dropdown + voice field writing these
  keys; the app build hard-sets the backend and hides the fields — same
  Protocol + registry underneath, nothing forks.
- Startup health report probes the *selected* backend (genie → HTTP ping,
  piper → voice files load), same 3s-non-blocking pattern as the LLM probes.

### D6. Fillers render through the registry

`scripts/generate_fillers.py` builds its client via the same registry+config
instead of hardcoding `GenieTTSClient`, so pre-rendered filler audio matches
the active voice. Existing checked-in fillers were rendered with Genie; the
default-voice install should regenerate them (task, not runtime logic).

## Risks / Trade-offs

- [Piper blocks the event loop] → all synthesis in `asyncio.to_thread`;
  chunks crossed back via queue. Verified by the existing latency tracker
  (`tts_first_chunk`) staying in budget.
- [First-run voice download needs network] → best-effort with bounded
  timeout, degrade-with-message on failure (D3); packaging-deploy may bundle
  voices to eliminate it.
- [Linear resampling quality] → inaudible for speech upsampling; documented
  ceiling with soxr as the upgrade path.
- [`piper-tts` API churn between majors] → import confined to the factory +
  client module; version pin owned by packaging-deploy (flagged seam).
- [Existing Genie installs silently switch voice after upgrade] → BREAKING
  callout in proposal/README migration note; genie path itself untouched, so
  recovery is one env var.
- [Filler voice mismatch with new default] → regenerate fillers with the
  Piper default voice as part of this change.

## Migration Plan

1. Land Protocol + registry + `TTSStage` with genie factory only — pure
   refactor, all existing tests green (deployable checkpoint).
2. Add Piper client + default selection + config/GUI keys.
3. Regenerate fillers; update `.env.example` and README migration note
   (`TTS_BACKEND=genie` for existing voice-clone setups).

Rollback at any point = select `genie`; the old code path is unchanged.

## Open Questions

- Exact stock voice ids for `female`/`male` aliases — pick by listening test
  at implementation time (medium-quality en_US voices).
- Whether packaging-deploy bundles voices or keeps first-run download — this
  design works with either; only D3's download path becomes dead code if
  bundled.
