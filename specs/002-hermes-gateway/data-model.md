# Data Model & Interfaces: Hermes Gateway Integration

## Entities

### `GatewayClient` (Base Class)
An abstract interface defining the core interactions with an AI backend.
- **Methods**:
  - `async def send_transcript(self, text: str)`: Abstract async generator that yields streamed audio response tokens/text.
  - `async def sessions_abort(self)`: Signals the backend to abort the current generation (used for barge-in).

### `HermesClient` (Implementation)
- **Inherits**: `GatewayClient`
- **Fields**:
  - `base_url`: URL of the Hermes Agent (default: `http://localhost:8642/v1`).
  - `session_id`: Unique `X-Hermes-Session-Id` maintained for the session.
  - `client`: `httpx.AsyncClient` instance for SSE streaming.

### `OpenClawClient` (Implementation)
- **Inherits**: `GatewayClient`
- **Description**: Existing logic refactored to implement the common interface.

## Configuration
- `gateway_backend`: Env var `GATEWAY_BACKEND` (values: `"hermes"` or `"openclaw"`, default: `"hermes"`).
- `hermes_base_url`: Env var `HERMES_BASE_URL` (default: `"http://localhost:8642/v1"`).
