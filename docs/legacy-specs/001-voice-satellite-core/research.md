# Research: Voice Satellite Core Client
**Feature**: `001-voice-satellite-core` | **Date**: 2026-05-19
## Research Tasks Resolved
### R1: VAD Library Version & WASM Compatibility
**Decision**: Use `@ricky0123/vad-web` v0.0.24+ with `onnxruntime-web` 1.14.0
**Rationale**: The legacy project's VAD (v0.0.18-lean) had a critical bug
where the `FrameProcessor` failed to pass `state` tensors to the Silero v5
ONNX model (see `VAD_TROUBLESHOOTING.md`). Version 0.0.24+ includes the fix
for v5 model state tensor handling. The reference project
(`open-llm-vtuber-web`) confirms this combination works.
**Alternatives considered**:
- v0.0.30 (latest): Works but bundles separate `models/v5.js` and
  `models/legacy.js` — added complexity for no benefit since we only need v5.
- v0.0.18-lean (current): Broken. `FrameProcessor.process()` omits state
  tensors.
**Key files**: `static/libs/vad.bundle.min.js`, `static/libs/ort.js`,
`static/libs/silero_vad_v5.onnx`
---
### R2: WASM Asset Serving (CDN Independence)
**Decision**: Serve all WASM files locally via the satellite's own static file
server. Set `onnxWASMBasePath: ASSET_PATH` explicitly in `MicVAD.new()`.
**Rationale**: The VAD library defaults to fetching `ort-wasm-simd.wasm` from
`cdn.jsdelivr.net`. This fails in environments with ad-blockers, tracking
protection, or offline operation (common on edge devices). Local serving
ensures zero CDN dependencies per FR-005.
**Alternatives considered**:
- CDN fallback: Violates FR-005 (no CDN dependency). Rejected.
- Bundled inline WASM (base64): 10 MB base64 in HTML. Rejected.
---
### R3: OpenWakeWord ONNX Pipeline Architecture
**Decision**: Three-stage pipeline (melspectrogram → embedding → classifier)
running as separate ONNX sessions in the browser, with adaptive threading.
**Rationale**: OpenWakeWord's architecture requires three distinct ONNX models
that execute sequentially. Each model has different input/output shapes:
- `melspectrogram.onnx`: `[1, 1280]` float32 → mel frames `[1, N, 32]`
- `embedding_model.onnx`: `[1, 76, 32, 1]` float32 → `[1, 96]` embedding
- `model.onnx`: `[1, 16, 96]` float32 → `[1]` score
The mel-spectrogram output requires a transform `(x/10 + 2)` before feeding
to the embedding model. Embeddings accumulate in a sliding window (76 frames,
stride 8). Classifier takes 16 consecutive embeddings.
**Adaptive threading** (from clarification Q2): Benchmark 10 passes at
startup. If avg > 30 ms/frame, offload to Web Worker.
**Alternatives considered**:
- Single combined model: OpenWakeWord doesn't provide one.
- Server-side wakeword: Adds network latency, violates FR-005.
---
### R4: Gateway Protocol Version Negotiation
**Decision**: Send `minProtocol` and `maxProtocol` in the connect request,
values configurable via `.env`. Default: min=3, max=4.
**Rationale**: The legacy code hard-pins `PROTOCOL_VERSION = 3` with
`minProtocol: 3, maxProtocol: 3`. This breaks on any gateway upgrade.
Configurable range allows forward compatibility without code changes.
**Legacy handshake flow** (from `openclaw_gateway.py`):
1. Client sends: `{"type": "connect", "token": "...", "minProtocol": 3, "maxProtocol": 4}`
2. Server responds: `{"type": "challenge", "nonce": "..."}`
3. Client signs nonce with Ed25519 key, sends `{"type": "challenge_response", ...}`
4. Server responds: `{"type": "connected", "protocol": 3}`
**Alternatives considered**:
- Hard-pin v3: Fragile. Rejected.
- Accept any version: Risk of incompatible protocol. Rejected.
---
### R5: Audio Sample Rate Contract
**Decision**: Dual-rate boundary. 16 kHz for capture/VAD/STT. 32 kHz for
TTS output and client playback. No implicit resampling.
**Rationale**: From clarification Q1. The legacy code already uses this
pattern successfully:
- VAD (Silero) expects 16 kHz input
- faster-whisper expects 16 kHz input
- Genie TTS outputs 32 kHz PCM (`GENIE_SAMPLE_RATE = 32000`)
- Frontend `AudioContext` is initialised at 32 kHz for playback
Forcing a single rate would require either upsampling captures (wasted CPU)
or downsampling TTS (degraded audio quality).
**Constants** (in `audio/constants.py`):
```python
CAPTURE_SAMPLE_RATE = 16_000
TTS_SAMPLE_RATE = 32_000
PCM_SAMPLE_WIDTH = 2  # bytes per sample (16-bit)
```
---
### R6: Single-Session Enforcement Strategy
**Decision**: Module-level `asyncio.Lock` in `server.py`. First connection
acquires the lock. Subsequent connections receive an error and are closed.
**Rationale**: From clarification Q4. The satellite is a single-microphone
ambient device. Multiple concurrent sessions would contend for Whisper (CPU)
and TTS resources, degrading all sessions on constrained hardware.
**Implementation pattern**:
```python
_session_lock = asyncio.Lock()
async def websocket_endpoint(ws: WebSocket):
    if _session_lock.locked():
        await ws.accept()
        await ws.send_json({"type": "error", "message": "Session already active"})
        await ws.close(code=1013)
        return
    async with _session_lock:
        # ... handle session
```
**Alternatives considered**:
- Queue-based: Added complexity for no user value (who queues a voice session?). Rejected.
- Read-only observer: Useful but out of scope for core feature. Deferred.
---
### R7: Sentence Splitter Design
**Decision**: Regex-based splitter that handles both standard sentence
boundaries and custom XML-style tags.
**Rationale**: The legacy `genie_tts.py` uses a simple `re.split` on
`(?<=[.!?])\s+` but also needs to handle `<glitch>...</glitch>` tags for
the character personas. Tagged segments must be split out as independent synthesis
units so audio effects can be applied per-segment.
**Split order**:
1. Extract `<glitch>...</glitch>` spans as separate chunks (tagged)
2. Split remaining text at sentence boundaries (`.!?` followed by whitespace)
3. Filter empty/whitespace-only/non-alphanumeric chunks
4. Ensure trailing punctuation on each chunk
---
### R8: Barge-in Word Offset Tracking
**Decision**: Backend tracks cumulative word count per TTS chunk. Frontend
reports `words_played` count at interrupt time. Backend reconstructs partial
response by slicing the word list.
**Rationale**: From legacy `session.py` and frontend `wordsPlayedAtTime` map.
Each TTS audio chunk is annotated with its starting word offset. The frontend
maps playback time → word offset. On barge-in, the frontend sends the last
word offset that was actually played, and the backend uses this to slice the
full response into "heard" and "unheard" parts.
**Data flow**:
1. Backend: split response into words, track offset per TTS chunk
2. Server→Client: `{"type": "audio", "word_offset": N, ...}`
3. Client: maps `audioStartTime → wordOffset` in `wordsPlayedAtTime`
4. On interrupt: `getWordsPlayedNow()` → last offset before `audioContext.currentTime`
5. Client→Server: `{"type": "playback_progress", "words_played": N}`
6. Backend: `partial = " ".join(words[:N])` → inject into LLM history
---
### R9: Filler Engine Pre-rendering
**Decision**: Pre-render filler audio offline via `generate_fillers.py`.
Load from `static/fillers/<category>/<slug>.pcm` at startup into RAM.
**Rationale**: Synthesizing fillers on-demand would defeat their purpose
(masking latency). Pre-rendered PCM files load in <1ms and play from RAM
with zero synthesis latency, meeting SC-006 (<50 ms playback start).
**Categories**: thinking, working, slow_task, acknowledge, signoff
**Filler trigger**: `asyncio.wait` with 2s timeout on the first TTS chunk.
If timeout fires first, play a random filler. Cancel filler if real response
arrives.
---
### R10: Configuration Validation Strategy
**Decision**: Fail-fast at startup for security-sensitive values (tokens). Warn-and-degrade for non-critical values (TTS URL, filler dir).
**Required (fail-fast)**: `OPENCLAW_GATEWAY_TOKEN`
**Required for TTS (warn if missing)**:
`GENIE_TTS_URL`, `GENIE_CHARACTER_NAME`, `GENIE_ONNX_MODEL_DIR`,
`GENIE_REFERENCE_AUDIO`, `GENIE_REFERENCE_TEXT`
**Optional with defaults**: `WHISPER_MODEL`, `FILLER_DIR`,
`FILLER_THRESHOLD_SECS`, `HOST`, `PORT`
---
### R11: LLM Coordinator Realignment & Latency Optimization
**Decision**: Connect directly to OpenClaw via a persistent WebSocket client reused across turns. Remove LiteLLM local router. Prepend barge-in interruption context to the next message.
**Rationale**: Eliminating the LiteLLM router saves a round-trip LLM call, reducing baseline latency by ~1-2 seconds. Reusing a persistent WebSocket connection bypasses the handshake negotiation (challenge/nonce signing) which took ~100-200ms per turn. Context is kept unified in OpenClaw's session.