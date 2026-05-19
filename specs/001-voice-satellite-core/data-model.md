# Data Model: Voice Satellite Core Client
**Feature**: `001-voice-satellite-core` | **Date**: 2026-05-19
## Entities
### SatelliteConfig
Frozen dataclass loaded once at startup from `.env`. Consumed by all modules.
| Field | Type | Source | Required | Default |
|-------|------|--------|----------|---------|
| litellm_api_key | str | `LITELLM_API_KEY` | ✅ | — |
| litellm_url | str | `LITELLM_URL` | — | `https://llm.frizzt.com/v1` |
| llm_model | str | `LLM_MODEL` | — | `qwen-moe-coder-fast` |
| llm_history_messages | int | `LLM_HISTORY_MESSAGES` | — | `20` |
| gateway_url | str | `OPENCLAW_GATEWAY_URL` | — | `http://127.0.0.1:18789` |
| gateway_token | str | `OPENCLAW_GATEWAY_TOKEN` | ✅ | — |
| gateway_min_protocol | int | `GATEWAY_MIN_PROTOCOL` | — | `3` |
| gateway_max_protocol | int | `GATEWAY_MAX_PROTOCOL` | — | `4` |
| tts_url | str | `GENIE_TTS_URL` | — | `http://127.0.0.1:8000` |
| tts_character_name | str | `GENIE_CHARACTER_NAME` | — | `ordis` |
| tts_onnx_model_dir | str | `GENIE_ONNX_MODEL_DIR` | ⚠️ | — |
| tts_reference_audio | str | `GENIE_REFERENCE_AUDIO` | ⚠️ | — |
| tts_reference_text | str | `GENIE_REFERENCE_TEXT` | ⚠️ | — |
| tts_language | str | `GENIE_LANGUAGE` | — | `en` |
| whisper_model | str | `WHISPER_MODEL` | — | `tiny.en` |
| whisper_language | str | `WHISPER_LANGUAGE` | — | `en` |
| filler_dir | Path | `FILLER_DIR` | — | `static/fillers` |
| filler_threshold_secs | float | `FILLER_THRESHOLD_SECS` | — | `2.0` |
| host | str | `HOST` | — | `0.0.0.0` |
| port | int | `PORT` | — | `8000` |
| skip_genie_init | bool | `VOICE_SATELLITE_SKIP_GENIE_INIT` | — | `False` |
✅ = fail-fast if missing  | ⚠️ = warn + degraded TTS mode if missing
---
### SessionState (Enum)
States of the conversation state machine. One instance per active WebSocket
connection.
| Value | Description | Transitions To |
|-------|-------------|---------------|
| `passive_listening` | Monitoring wakeword only | `acknowledging` |
| `acknowledging` | Playing ack filler | `active_listening` |
| `active_listening` | Full VAD speech capture | `transcribing`, `passive_listening` |
| `transcribing` | Whisper STT in progress | `thinking`, `active_listening` |
| `thinking` | LLM routing in progress | `speaking`, `filler_speaking`, `passive_listening` |
| `filler_speaking` | Playing latency filler | `speaking`, `interrupted` |
| `speaking` | Playing TTS audio | `active_listening`, `interrupted` |
| `interrupted` | Barge-in — flushing buffers | `active_listening` |
---
### ConversationSession
Per-connection state machine instance.
| Field | Type | Description |
|-------|------|-------------|
| state | SessionState | Current state |
| generation_task | asyncio.Task \| None | Active audio generation pipeline |
| silence_timer | asyncio.Task \| None | Returns to passive on expiry |
| silence_timeout | float | Configurable 10–120 s, default 30 |
| current_response_words | list[str] | Full word list of current response |
| words_played_before_interrupt | int | Reported by frontend on barge-in |
---
### TrackedTask
Background OpenClaw agent task.
| Field | Type | Description |
|-------|------|-------------|
| task_id | str | UUID-based identifier (e.g., `task-abc12345`) |
| agent_id | str | OpenClaw agent (`main` \| `ugin`) |
| description | str | Human-readable task summary |
| status | Literal["running", "completed", "failed"] | Current state |
| result | str \| None | Collected text output on completion |
| error | str \| None | Error message on failure |
| started_at | float | `time.monotonic()` timestamp |
| asyncio_task | asyncio.Task | Handle for cancellation |
---
### CoordinatorDecision
LLM router output.
| Field | Type | Description |
|-------|------|-------------|
| action | str | `answer` \| `openclaw` \| `check_tasks` \| `conversation_end` |
| message | str | Response text (spoken answer or agent instruction) |
| reason | str | Short routing rationale (logged, not spoken) |
| openclaw | RouterDecision \| None | Populated when action=openclaw |
---
### RouterDecision
OpenClaw routing parameters (nested in CoordinatorDecision).
| Field | Type | Description |
|-------|------|-------------|
| agent_id | str | Target agent (`main` \| `ugin`) |
| mode | str | `one_shot` \| `persistent` |
| message | str | Instruction to send to the agent |
| session_key | str | Default `voice` |
---
### AudioChunk (Conceptual)
Not a persisted entity — represents the data flowing between pipeline stages.
| Field | Type | Description |
|-------|------|-------------|
| pcm_data | bytes | Raw PCM audio (16-bit signed LE mono) |
| sample_rate | int | 16000 (capture) or 32000 (TTS) |
| word_offset | int \| None | Cumulative word count at this chunk's start |
| tagged | bool | Whether this chunk has effect tags applied |
---
### WakewordModel (Frontend)
Configuration from `static/wakeword/model.json`.
| Field | Type | Description |
|-------|------|-------------|
| file | str | Classifier ONNX filename (e.g., `model.onnx`) |
| name | str | Human-readable wakeword name (e.g., `Hey Ordis`) |
| sample_rate | int | Expected audio sample rate (16000) |
| threshold | float | Detection confidence threshold (0.0–1.0) |
---
## Relationships
```mermaid
erDiagram
    SatelliteConfig ||--o| ConversationSession : "configures"
    SatelliteConfig ||--o| GenieTTSClient : "configures"
    SatelliteConfig ||--o| OpenClawClient : "configures"
    SatelliteConfig ||--o| LLMRouter : "configures"
    SatelliteConfig ||--o| WhisperSTT : "configures"
    SatelliteConfig ||--o| FillerEngine : "configures"
    ConversationSession ||--o{ TrackedTask : "tracks"
    ConversationSession ||--|| SessionState : "has current"
    ConversationSession }o--|| LLMRouter : "routes via"
    LLMRouter ||--|| CoordinatorDecision : "produces"
    CoordinatorDecision ||--o| RouterDecision : "contains (openclaw)"
    GenieTTSClient }o--|| AudioChunk : "produces"
    FillerEngine }o--|| AudioChunk : "produces"
    WhisperSTT }o--|| ConversationSession : "transcribes for"
```
---
## Validation Rules
1. **SatelliteConfig**: `litellm_api_key` and `gateway_token` MUST be
   non-empty strings. Application MUST exit with code 1 and a human-readable
   error if either is missing.
2. **SessionState transitions**: Only transitions defined in the state
   machine diagram are valid. Invalid transitions MUST log a warning and
   be ignored (never crash).
3. **TrackedTask.task_id**: MUST be unique across the lifetime of a
   `TaskTracker` instance. Generated via `uuid4().hex[:8]`.
4. **AudioChunk.sample_rate**: MUST be either `CAPTURE_SAMPLE_RATE` (16000)
   or `TTS_SAMPLE_RATE` (32000). Any other value is a programming error.
5. **WakewordModel.threshold**: MUST be in range [0.0, 1.0]. Values outside
   this range MUST be clamped with a warning.
6. **CoordinatorDecision.action**: MUST be one of the known action strings.
   Unknown actions MUST be treated as `answer` (safe fallback).