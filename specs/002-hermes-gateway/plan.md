# Implementation Plan: Hermes Gateway Integration & Pipeline Optimizations

**Branch**: `feat/hermes-gateway` | **Date**: 2026-05-29 | **Spec**: [specs/002-hermes-gateway/spec.md](specs/002-hermes-gateway/spec.md)

## Summary

Integrate Hermes Agent as the primary AI backend using an OpenAI-compatible HTTP SSE API with session continuity (`X-Hermes-Session-Id`), while preserving the existing OpenClaw WebSocket backend as a swappable configuration option. Optimize the downstream audio delivery pipeline to achieve sub-second latency by implementing a parallel ordered TTS queue, a persistent HTTP client session for Genie TTS, clause-level sentence splitting, and memory-preserving interruption handling.

## Technical Context

**Language/Version**: Python 3.11

**Primary Dependencies**: `httpx` for HTTP SSE streaming, `aiohttp` for local TTS requests, FastAPI for local WebSocket endpoints

**Storage**: Local memory (session state machine/history buffers)

**Testing**: `pytest`

**Target Platform**: Linux server / PC / Raspberry Pi

**Performance Goals**: Time-to-first-audio under 400ms, inter-sentence delay under 50ms.

## Proposed Architectural Changes

### 1. Parallel Ordered TTS Pipeline (`TTSTaskManager`)
Introduce a helper class `TTSTaskManager` in `voice_satellite/tts/manager.py` (or integrated into `server.py`/session orchestration) that:
- Maintains an incrementing `sequence_counter` and `next_sequence_to_send` pointer.
- Exposes a non-blocking `enqueue_tts(text, seq_num)` method that dispatches an asynchronous task to synthesize audio for a single sentence.
- Feeds synthesized audio chunks into an internal `asyncio.Queue` labeled with the corresponding sequence number.
- Runs a background worker `_send_loop` that reads from the queue, buffers out-of-order chunks, and streams them over the WebSocket client strictly in sequential order.

### 2. Persistent HTTP Sessions in `GenieTTSClient`
Refactor `voice_satellite/tts/genie_client.py`:
- Store a single `aiohttp.ClientSession` instance inside `GenieTTSClient` as a persistent connection pool.
- Reuse this session for all `POST /tts` calls to Genie TTS instead of creating a new session per sentence.
- Ensure proper resource cleanup during client shutdown (close the session).

### 3. Clause-Level Sentence Splitting
Refactor `voice_satellite/tts/sentence_splitter.py`:
- Update `split_sentences` to split on clause boundaries (commas `,`, semicolons `;`, colons `:`, and em-dashes `—`) in addition to sentence endings (`.`, `!`, `?`).
- Only apply clause-level splitting if the accumulated text before the clause boundary exceeds 8 words to prevent overly micro-fragmented audio streams.

### 4. Contextual Barge-in & Interruption Memory
Refactor `voice_satellite/server.py` and `voice_satellite/gateway/hermes_client.py`:
- When user speech starts (VAD active), cancel all active LLM streaming and TTS queue tasks.
- Keep the `X-Hermes-Session-Id` intact.
- Calculate the exact text that was successfully sent to playback before interruption occurred (using word offset metadata).
- Inject the partial text actually spoken by the assistant back into the agent/session history as a completed assistant turn, appended with a flag `[Interrupted by user]`, before handling the new user prompt.

## Project Structure

### Documentation (this feature)

```text
specs/002-hermes-gateway/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── tasks.md
```

### Source Code (repository root)

```text
voice_satellite/
├── gateway/
│   ├── base.py              # GatewayClient base class
│   ├── openclaw_client.py   # Existing openclaw client implementing GatewayClient
│   └── hermes_client.py     # New hermes client implementing GatewayClient
├── tts/
│   ├── genie_client.py      # Updated to use persistent ClientSession
│   ├── manager.py           # [NEW] TTSTaskManager for parallel ordered delivery
│   └── sentence_splitter.py # Updated for clause-level splitting
├── server.py                # Updated to use GatewayClient factory, producer-consumer queue, and barge-in memory
└── config.py                # Updated for new backend toggle
tests/
└── unit/
    ├── test_hermes_client.py
    ├── test_tts_manager.py
    ├── test_sentence_splitter.py
    └── test_config.py
```

## Verification Plan

### Automated Tests
- `PYTHONPATH=. python -m pytest tests/unit/test_sentence_splitter.py` to verify clause-level splitting rules.
- `PYTHONPATH=. python -m pytest tests/unit/test_tts_manager.py` to verify that out-of-order synthesis results are sent in order.
- `PYTHONPATH=. python -m pytest tests/unit/test_hermes_client.py` to test session management and SSE decoding.
- Run complete test suite: `PYTHONPATH=. python -m pytest tests/`

### Manual Verification
- Run the Voice Satellite: `python -m voice_satellite`
- Connect a browser-based client or test tool.
- Speak a long prompt, check that audio response starts sooner (clause-level splitting).
- Confirm that multiple sentences flow smoothly without pause (parallel prefetch).
- Interrupt mid-sentence and verify context memory on the next turn.

## References & Reference Implementations

This design is inspired by the streaming pipeline and interruption mechanics of the following repositories:

- **Backend Repository**: [Open-LLM-VTuber/Open-LLM-VTuber](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber)
  - **Server & Streaming Coordinator**: [src/open_llm_vtuber/server.py](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber/blob/main/src/open_llm_vtuber/server.py) (Coordinates LLM and TTS tasks asynchronously).
  - **TTS Integrations & Chunking**: [src/open_llm_vtuber/tts/](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber/tree/main/src/open_llm_vtuber/tts) (Shows chunk/sentence generation modules).
  - **Agent State & Memory Management**: [src/open_llm_vtuber/agent/](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber/tree/main/src/open_llm_vtuber/agent) (Handles conversational turns and history updates).
- **Web Frontend Repository**: [Open-LLM-VTuber/Open-LLM-VTuber-Web](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber-Web)
  - **Audio Playback Queue & Client VAD**: [src/](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber-Web/tree/main/src) (Client-side handling of WebSocket audio stream chunks and barge-in triggers).

