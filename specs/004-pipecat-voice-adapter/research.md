# Research: Pipecat Voice Adapter

**Branch**: `pipecat` | **Date**: 2026-06-02 | **Spec**: [spec.md](spec.md)

## 1. Pipecat Framework Architecture

### Decision: Use Pipecat `FastAPIWebsocketTransport`

**Rationale**: The user chose FastAPIWebsocketTransport over SmallWebRTCTransport because all communication occurs over Tailscale LAN where WebRTC's NAT traversal advantages are irrelevant. FastAPIWebsocketTransport keeps the architecture closer to the existing voice_satellite WebSocket model, avoids a full frontend rewrite to WebRTC, and integrates directly into a FastAPI routing layer.

**Alternatives considered**:
- SmallWebRTCTransport: Better latency on unreliable networks, but unnecessary overhead for Tailscale LAN. Would require a WebRTC-capable client process.
- Custom transport: Maximum control but reinvents what Pipecat already provides.

### Pipecat Core Concepts

- **Frames**: Data packets (audio, text, control) flowing through the pipeline
- **Processors**: Modular workers that receive, transform, and emit frames
- **Pipeline**: Ordered sequence of processors from input to output
- **PipelineTask**: Manages the lifecycle of a pipeline execution
- **PipelineRunner**: Orchestrates tasks and handles clean shutdown

### FastAPIWebsocketTransport Integration

```python
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketTransport,
    FastAPIWebsocketParams,
)

transport = FastAPIWebsocketTransport(
    websocket=websocket,
    params=FastAPIWebsocketParams(
        audio_in_sample_rate=16000,   # microphone capture rate
        audio_out_sample_rate=32000,  # TTS output rate (Genie)
    ),
)
```

Key details:
- Transport wraps a FastAPI WebSocket connection
- `transport.input()` emits `AudioRawFrame` from the client
- `transport.output()` sends `AudioRawFrame` back to the client
- Handles VAD internally or can be configured with custom VAD
- Supports interruption/barge-in natively

### Custom TTSService Subclass

```python
from pipecat.services.tts_service import TTSService
from pipecat.frames.frames import AudioRawFrame

class GenieTTSService(TTSService):
    async def run_tts(self, text: str, context_id: str):
        # Call Genie TTS HTTP server
        async for audio_chunk in self._genie_client.synthesize(text):
            yield AudioRawFrame(
                audio=audio_chunk,
                sample_rate=32000,
                num_channels=1,
            )
```

Text aggregation is handled by Pipecat — it buffers LLM tokens into sentences before calling `run_tts`, which aligns with how we currently use `split_sentences()`.

### LLM Service via Hermes Gateway

Since Hermes exposes an OpenAI-compatible endpoint, we can use Pipecat's built-in `OpenAILLMService` with a custom `base_url`:

```python
from pipecat.services.openai import OpenAILLMService

llm = OpenAILLMService(
    api_key=config.hermes_api_key or "not-needed",
    model=config.hermes_model,
    base_url=config.hermes_base_url,
)
```

This replaces the need for the adapter to manually handle SSE parsing for direct responses — Pipecat does it natively. The existing `HermesClient` is still needed for:
- Task dispatch (non-streaming, fire-and-forget calls)
- Session management (`X-Hermes-Session-Id`)
- SSE listening for background task completions (pipecat_bridge)

## 2. Component Reuse Analysis

### Fully Reusable (import as-is)

| Component | File | Usage in Adapter |
|-----------|------|-----------------|
| `GenieTTSClient` | `voice_satellite/tts/genie_client.py` | Backend for `GenieTTSService.run_tts()` |
| `effects.py` | `voice_satellite/audio/effects.py` | Post-processing on TTS audio frames |
| `sentence_splitter.py` | `voice_satellite/tts/sentence_splitter.py` | May be needed if Pipecat's aggregation is insufficient |
| `SatelliteConfig` | `voice_satellite/config.py` | Extended for adapter-specific fields |

### Replaced by Pipecat

| Component | File | Pipecat Equivalent |
|-----------|------|--------------------|
| `server.py` WebSocket handler | `voice_satellite/server.py` | `FastAPIWebsocketTransport` |
| `WhisperSTT` | `voice_satellite/stt/whisper_stt.py` | Pipecat's STT processor with faster-whisper |
| `ConversationSession` | `voice_satellite/session/state_machine.py` | Pipecat's PipelineTask + adapter state |
| `TTSTaskManager` | `voice_satellite/tts/manager.py` | Pipecat's frame pipeline handles ordering |

### Still needed separately

| Component | Reason |
|-----------|--------|
| `HermesClient` | Session management, task dispatch, SSE listener (not covered by Pipecat's `OpenAILLMService`) |
| `LatencyTracker` | Telemetry — plugs into Pipecat's observer pattern |

## 3. Offline Handler Design

### Decision: Time-based check + live probe

**Rationale**: Using both a time-based check (1 AM–5 AM) and a live HTTP probe to the LiteLLM endpoint provides defense in depth. The time check avoids unnecessary probe traffic during known downtime; the probe handles unexpected outages.

**LiteLLM probe**: `GET /health` on the LiteLLM endpoint (configurable URL). Timeout 2 seconds. If unreachable → offline mode.

**Command classification**: The adapter passes the user's transcript to a simple keyword/intent classifier:
- State-changing keywords: "deploy", "restart", "turn on/off", "set", "delete", "run"
- Deferrable keywords: "remind", "what is", "tell me about", "check", "find"
- Ambiguous: defaults to deferrable (safer to queue than to fail)

### Disk-backed queue

JSON file at `~/.hermes/offline_queue.json`:
```json
{
  "entries": [
    {
      "timestamp": "2026-06-02T02:15:30Z",
      "transcript": "remind me to check the server logs tomorrow",
      "classification": "deferrable",
      "status": "queued"
    }
  ]
}
```

## 4. Pre-Fetch Cache Architecture

### Decision: Local inotify + Honcho HTTP polling

**Rationale**: NFS/SSHFS inotify is unreliable on Linux. Local watchdog for `~/.hermes/memory` gives sub-second updates for local state. Honcho HTTP polling every 20–30 seconds is sufficient — memory synthesis doesn't change faster than that.

**Cache structure**:
```python
@dataclass
class ContextSnapshot:
    user_profile: dict          # from ~/.hermes/memory/profile.json
    recent_memories: list[str]  # from Honcho API
    pending_tasks: list[dict]   # from transcript_manager
    last_updated: datetime
```

**Warm gate**: The adapter registers a callback with the cache. Before allowing wake word activation, it checks `cache.is_warm`. If cold, it queues a hydration task and blocks.

## 5. Pipecat STT Integration

### Decision: Use Pipecat's native STT support with faster-whisper

Pipecat has a `WhisperSTTService` (or compatible processor) that wraps faster-whisper. This replaces our custom `WhisperSTT` class. The adapter configures:

```python
stt = WhisperSTTService(
    model=config.whisper_model,   # "tiny.en"
    language=config.whisper_language,  # "en"
)
```

Audio flows: Transport → STT → LLM → TTS → Transport

## 6. Wake Word Integration

### Decision: Custom FrameProcessor for openWakeWord

Pipecat has `WakePhraseUserTurnStartStrategy` but it works on STT transcriptions, not raw audio. Since we use openWakeWord with a custom "Renna" ONNX model operating on raw audio frames, we need a custom `FrameProcessor` that:

1. Receives `AudioRawFrame` from transport input
2. Passes audio to the openWakeWord model
3. On detection, emits a control frame that activates the STT pipeline
4. While not detected, drops audio frames (preventing STT processing)

This preserves the existing on-device, pre-STT wake word detection behavior.
