"""vocascade.transport — WS codec, transport-auth gate, and the wire-protocol version."""

# Version of the edge<->host WebSocket contract (docs/protocol.md). Single
# source for server and edge client; any breaking wire change increments it.
PROTOCOL_VERSION = 1
