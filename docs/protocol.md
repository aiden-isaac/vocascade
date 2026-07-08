# vocascade WebSocket protocol (v1)

The public wire contract between a vocascade host and any satellite client
(the shipped edge client, the browser client, or your own — Android, ESP32, …).
Everything a client needs is on this page; the server source is not required.

- **Endpoint**: `ws://<host>:<port>/ws` (default port `8005`)
- **Protocol version**: `1` — single integer, exact match. Defined once as
  `vocascade.transport.PROTOCOL_VERSION`. Any breaking change to a message
  format or ordering rule on this page increments it (and updates this page)
  in the same change.
- **Forward compatibility**: clients MUST ignore JSON messages whose `type`
  they don't recognize. New informational types may appear within a version.

## Connection sequence

```
client                                server
  │  ── WS connect ──────────────────▶ │
  │  ◀───────── {"type":"hello","protocol_version":1} ──  (always first)
  │                                    │
  │        (device-identity mode only) │
  │  ◀───────── {"type":"auth_challenge","nonce":"…"} ──  │
  │  ── {"type":"auth_response","public_key":"…",         │
  │      "signature":"…"} ───────────▶ │
  │  ◀───────── {"type":"auth_ok"}  (or auth_error + close 1008)
  │                                    │
  │  ── {"type":"wakeword"} ─────────▶ │   session is live
```

1. **hello** — the first frame on *every* accepted connection, in every auth
   mode, even connections about to be rejected. A client checks
   `protocol_version` against its own: on mismatch, disconnect and tell the
   user to update host and client together. A server that never sends hello
   predates protocol versioning.
2. **Transport auth** — governed by the host's `transport_auth_mode`:
   - `trust-network`: no auth traffic; the session is live after hello.
   - `device-identity`: the server sends `auth_challenge` with a `nonce`; the
     client signs the nonce with its Ed25519 device key and replies with
     `auth_response` (`public_key`, `signature` — both base64). The server
     answers `auth_ok`, or `auth_error` + close **1008**.
3. **Single session**: one active connection per host. A concurrent connect is
   accepted, receives hello, then `{"type":"error","message":"Session already
   active. …"}`, and is closed with **1008**.

## Client → server

| Message | Meaning |
|---|---|
| *binary frame* | One complete utterance: raw PCM, signed 16-bit little-endian, mono, at the host's `AUDIO_IN_SAMPLE_RATE` (default **16000 Hz**). The client endpoints the utterance (its own VAD/silence rule) and sends it as a single binary message. |
| `{"type":"wakeword"}` | Wake word detected — opens/re-arms the turn. The server replies with a `status: active_listening` and may play a pre-rendered acknowledgement. |
| `{"type":"interrupt"}` | Barge-in: abort in-flight speech/synthesis and cancel the session's in-flight background tasks. |
| `{"type":"auth_response", "public_key", "signature"}` | Device-identity handshake reply (see above). |
| `{"type":"set_timeout", "seconds"}` | Advisory silence-timeout hint. Currently ignored by the server. |
| `{"type":"playback_progress", "words_played"}` | Advisory playback telemetry. Currently ignored by the server. |

## Server → client

All server frames are JSON text. A minimal voice client only needs `audio`
(and `hello`); the rest are informational.

| Message | Meaning |
|---|---|
| `{"type":"hello", "protocol_version"}` | First frame on every connection. |
| `{"type":"audio", "data", "sample_rate"}` | Speech to play: `data` is base64 raw PCM (s16le, mono) at `sample_rate` (default **32000 Hz**, the host's `AUDIO_OUT_SAMPLE_RATE`). |
| `{"type":"audio_end"}` | The current spoken reply is complete. |
| `{"type":"flush_audio"}` | Drop any buffered playback immediately (barge-in). |
| `{"type":"status", "state"}` | Session state: `active_listening`, `assistant_streaming`, or `passive_listening`. |
| `{"type":"transcript", "text"}` | STT result for the last utterance. |
| `{"type":"decision", …}` | Which waterfall stage claimed the utterance (debug/UI). |
| `{"type":"assistant_response", "text"}` / `{"type":"assistant_delta", …}` | The reply text (full / streaming delta). |
| `{"type":"tool_call_status", …}` / `{"type":"task_complete", …}` | Background (Hermes) task progress. |
| `{"type":"auth_challenge", "nonce"}` / `{"type":"auth_ok"}` / `{"type":"auth_error", "message"}` | Device-identity handshake (see above). |
| `{"type":"error", "message"}` | Connection-level rejection (e.g. session already active), followed by close 1008. |

## Close codes

| Code | Meaning |
|---|---|
| 1008 | Policy violation: transport auth failed, or a session is already active. |

## Versioning policy

`protocol_version` is bumped for any change that would break a correctly
written v1 client: altering or removing a documented message field, changing
frame ordering, or changing audio encoding. Adding new JSON message types is
**not** breaking (clients ignore unknown types). Clients SHOULD refuse to run
on a mismatched version rather than guess.
