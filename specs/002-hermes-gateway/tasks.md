# Implementation Tasks: Hermes Gateway Integration

## Context
**Feature**: Hermes Gateway Integration
**Plan**: [specs/002-hermes-gateway/plan.md](specs/002-hermes-gateway/plan.md)
**Status**: Pending

## Phase 1: Gateway Abstraction & Configuration
**Goal**: Introduce the generic `GatewayClient` interface and adapt configuration to support multiple backends.
- [ ] **T001**: Add `gateway_backend` (`GATEWAY_BACKEND`) and `hermes_base_url` (`HERMES_BASE_URL`) to `voice_satellite/config.py`.
- [ ] **T002**: Create `voice_satellite/gateway/base.py` defining the `GatewayClient` abstract base class with `send_transcript` and `sessions_abort` methods.
- [ ] **T003** [P]: Rename the existing OpenClaw connection logic to `OpenClawClient` in `voice_satellite/gateway/openclaw_client.py` and make it implement the `GatewayClient` interface.
- [ ] **T004**: Update `tests/unit/test_config.py` to test the new configuration variables.

## Phase 2: Hermes Client Implementation
**Goal**: Implement the `HermesClient` that streams HTTP SSE from the Hermes Agent.
- [ ] **T005**: Create `voice_satellite/gateway/hermes_client.py` and implement the `HermesClient` inheriting from `GatewayClient`.
- [ ] **T006**: Implement HTTP SSE generation via `httpx.AsyncClient` inside `HermesClient.send_transcript`, parsing the OpenAI-compatible stream chunks.
- [ ] **T007** [P]: Implement `HermesClient.sessions_abort` to stop any active generators and generate a new `X-Hermes-Session-Id` UUID for barge-in resets.
- [ ] **T008**: Create `tests/unit/test_hermes_client.py` to mock `httpx` and verify SSE chunk parsing and session management.

## Phase 3: Server Integration & Factory
**Goal**: Update the FastAPI server to instantiate the correct client dynamically and handle failovers.
- [ ] **T009**: Modify `voice_satellite/server.py` to include a factory function that instantiates either `HermesClient` or `OpenClawClient` based on `config.gateway_backend`.
- [ ] **T010**: Update the WebSocket streaming loop in `voice_satellite/server.py` to call `GatewayClient.send_transcript` instead of hardcoded OpenClaw logic.
- [ ] **T011**: Ensure connection errors from either client are caught gracefully and passed to the TTS error-reporting flow.
- [ ] **T012**: Update `tests/unit/test_server.py` to patch the gateway client and verify the correct factory instantiation and streaming behavior.

## Phase 4: Final Verification
**Goal**: Validate that everything runs end-to-end.
- [ ] **T013**: Run the complete test suite.
- [ ] **T014**: Manual verification: start the server with Hermes, connect UI, speak, and interrupt. Verify logs for `X-Hermes-Session-Id` and latency.
