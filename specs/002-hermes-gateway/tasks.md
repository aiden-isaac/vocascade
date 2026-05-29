# Implementation Tasks: Hermes Gateway Integration & Pipeline Optimizations

## Context
**Feature**: Hermes Gateway Integration & Pipeline Optimizations
**Plan**: [specs/002-hermes-gateway/plan.md](specs/002-hermes-gateway/plan.md)
**Status**: Pending

## Phase 1: Gateway Abstraction & Configuration
**Goal**: Introduce the generic `GatewayClient` interface and adapt configuration to support multiple backends.
- [x] **T001**: Add `gateway_backend` (`GATEWAY_BACKEND`) and `hermes_base_url` (`HERMES_BASE_URL`) to `voice_satellite/config.py`.
- [x] **T002**: Create `voice_satellite/gateway/base.py` defining the `GatewayClient` abstract base class with `send_transcript` and `sessions_abort` methods.
- [x] **T003** [P]: Rename the existing OpenClaw connection logic to `OpenClawClient` in `voice_satellite/gateway/openclaw_client.py` and make it implement the `GatewayClient` interface.
- [x] **T004**: Update `tests/unit/test_config.py` to test the new configuration variables.

## Phase 2: Hermes Client Implementation
**Goal**: Implement the `HermesClient` that streams HTTP SSE from the Hermes Agent.
- [x] **T005**: Create `voice_satellite/gateway/hermes_client.py` and implement the `HermesClient` inheriting from `GatewayClient`.
- [x] **T006**: Implement HTTP SSE generation via `httpx.AsyncClient` inside `HermesClient.send_transcript`, parsing the OpenAI-compatible stream chunks.
- [x] **T007** [P]: Implement `HermesClient.sessions_abort` to stop any active generators and generate a new `X-Hermes-Session-Id` UUID for barge-in resets.
- [x] **T008**: Create `tests/unit/test_hermes_client.py` to mock `httpx` and verify SSE chunk parsing and session management.

## Phase 3: Server Integration & Factory
**Goal**: Update the FastAPI server to instantiate the correct client dynamically and handle failovers.
- [x] **T009**: Modify `voice_satellite/server.py` to include a factory function that instantiates either `HermesClient` or `OpenClawClient` based on `config.gateway_backend`.
- [x] **T010**: Update the WebSocket streaming loop in `voice_satellite/server.py` to call `GatewayClient.send_transcript` instead of hardcoded OpenClaw logic.
- [x] **T011**: Ensure connection errors from either client are caught gracefully and passed to the TTS error-reporting flow.
- [x] **T012**: Update `tests/unit/test_server.py` to patch the gateway client and verify the correct factory instantiation and streaming behavior.

## Phase 4: Final Verification of Gateway
**Goal**: Validate that everything runs end-to-end.
- [x] **T013**: Run the complete test suite.
- [x] **T014**: Manual verification: start the server with Hermes, connect UI, speak, and interrupt. Verify logs for `X-Hermes-Session-Id` and latency.

## Phase 5: Pipeline & TTS Optimizations (US4)
**Goal**: Implement parallel ordered TTS queuing, persistent HTTP client sessions, clause-level splitting, and eliminate double-splitting.
- [x] **T015** [US4]: Refactor `voice_satellite/tts/genie_client.py` to use a persistent `aiohttp.ClientSession` reused across requests, closing it gracefully on server shutdown.
- [x] **T016** [US4]: Update `voice_satellite/tts/sentence_splitter.py` to support clause-level splitting on `,`, `;`, `:`, `—` when the accumulated segment exceeds 8 words.
- [x] **T017** [US4]: Remove redundant double-splitting logic inside `voice_satellite/server.py`'s `speak_text_to_tts` and disable Genie payload `split_sentence` flag.
- [x] **T018** [P] [US4]: Implement `TTSTaskManager` in `voice_satellite/tts/manager.py` to run parallel TTS generation tasks and buffer/stream results over WebSocket in sequential order.
- [x] **T019** [US4]: Integrate `TTSTaskManager` and the non-blocking token producer/consumer `asyncio.Queue` loop inside `voice_satellite/server.py`.
- [x] **T020** [P] [US4]: Create `tests/unit/test_sentence_splitter.py` verifying clause-level splitting rules.
- [x] **T021** [P] [US4]: Create `tests/unit/test_tts_manager.py` verifying parallel synthesis task ordering and buffer delivery.

## Phase 6: Contextual Barge-in & Interruption Memory (US5)
**Goal**: Retain session memory on barge-in/interruption by avoiding session rotation and calculating the partial text spoken.
- [x] **T022** [US5]: Refactor barge-in handling in `voice_satellite/server.py` to preserve the `X-Hermes-Session-Id` and avoid session ID rotation.
- [ ] **T023** [US5]: Track the exact cumulative word offset of the audio played back, and compute the partial text spoken before interruption in `voice_satellite/server.py`.
- [ ] **T024** [US5]: Store the partial interrupted response in the session history under assistant role, appended with `[Interrupted by user]`, inside `voice_satellite/server.py`.
- [ ] **T025** [US5]: Perform manual verification of barge-in contextual retention.
