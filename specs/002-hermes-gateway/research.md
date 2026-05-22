# Research & Decisions: Hermes Gateway Integration

## Decision: AI Backend Interface
**Decision**: Implement a common `GatewayClient` base class that both `OpenClawClient` and `HermesClient` will inherit from.
**Rationale**: Allows the server to remain agnostic to the specific backend being used. The config will dictate which client to instantiate.
**Alternatives considered**: Modifying the existing OpenClawClient directly. Rejected because OpenClaw uses WebSockets while Hermes uses HTTP SSE.

## Decision: Hermes API Client
**Decision**: Use `httpx` to consume the HTTP SSE API of Hermes Agent.
**Rationale**: `httpx` has great async support which is necessary since our FastAPI server operates asynchronously.
**Alternatives considered**: Using the official `openai` python library. Rejected to avoid bloat and because `httpx` is already capable of handling simple SSE streaming for chat completions.

## Decision: Session Continuity
**Decision**: Generate a random UUID string as the `X-Hermes-Session-Id` header on the first connection and maintain it per conversational session in the server state.
**Rationale**: Hermes Agent requires this header to maintain conversation history between turns.
