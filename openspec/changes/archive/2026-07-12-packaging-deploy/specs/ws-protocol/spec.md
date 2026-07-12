# ws-protocol

The edge↔host WebSocket protocol as a documented public contract with an
explicit version exchanged in the handshake.

## ADDED Requirements

### Requirement: Protocol version in the handshake

A single protocol version constant (starting at 1) SHALL be defined in one
place and used by both server and edge client. Immediately after accepting a
`/ws` connection — before the transport-auth gate, in every auth mode — the
server SHALL send `{"type": "hello", "protocol_version": <int>}`. The edge
client SHALL read this message first and proceed only if the version equals
its own; on mismatch it SHALL log a clear "update host/edge together" error
and disconnect. Clients SHALL continue to ignore JSON messages of unknown
type, so `hello` is non-breaking for the browser client.

#### Scenario: matching versions proceed to auth and audio

- **WHEN** an edge client whose protocol version equals the server's connects
- **THEN** the hello is consumed, the existing auth handshake (trust-network
  pass-through or device-identity challenge) proceeds unchanged, and audio flows

#### Scenario: version mismatch fails loudly

- **WHEN** the server's `protocol_version` differs from the edge client's
- **THEN** the edge client logs an error identifying both versions and the
  remedy (update host and edge together) and closes the connection instead of
  streaming audio against an incompatible contract

### Requirement: Documented public wire contract

The repository SHALL contain `docs/protocol.md` documenting the complete `/ws`
wire contract as a stable public interface for third-party clients: connection
and hello ordering, both transport-auth modes (`auth_challenge` /
`auth_response` / `auth_ok`), binary PCM upstream audio, base64-JSON
downstream audio, control messages (`wakeword`, `interrupt`), server messages
(`status`, `error`), the single-session rejection (close code 1008), and the
versioning policy (any breaking wire change increments the protocol version).

#### Scenario: third-party client can be built from the doc alone

- **WHEN** a developer implements a new satellite client using only
  `docs/protocol.md`
- **THEN** every message type, ordering rule, and encoding they need is
  specified in the document without reading the server source

#### Scenario: breaking wire changes bump the version

- **WHEN** a future change alters any documented message format or ordering
  incompatibly
- **THEN** the protocol version constant is incremented and the document
  updated in the same change
