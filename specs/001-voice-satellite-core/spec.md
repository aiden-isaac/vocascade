# Feature Specification: Voice Satellite Core Client
**Feature Branch**: `001-voice-satellite-core`
**Created**: 2026-05-19
**Status**: Draft
## User Scenarios & Testing *(mandatory)*
### User Story 1 — Wakeword Activation & Voice Activity Detection (Priority: P1)
The user places a satellite device (laptop or Raspberry Pi) in a room. The
system continuously monitors ambient audio through the microphone. In passive
mode, only a lightweight wakeword detection model evaluates audio frames; all
other speech is discarded. When the user utters the configured wakeword
(e.g., "Hey Assistant"), the system immediately acknowledges (via a short
pre-rendered audio clip) and transitions to active listening mode. In active
mode, a Voice Activity Detection (VAD) model segments speech from silence.
When the user finishes speaking (VAD detects speech end), the captured audio
is forwarded to the backend for processing. After a configurable period of
silence (default 30 seconds, adjustable 10–120 s via UI), the system
automatically returns to passive mode.
**Why this priority**: Without wakeword and VAD, the satellite has no way to
accept user input. This is the foundational interaction layer upon which all
other features depend.
**Independent Test**: A user can boot the satellite, say the wakeword, speak
a phrase, and verify that the system transitions from passive → acknowledging
→ active listening → transcribing. No backend services (TTS, gateway) are
required for this test — only the microphone, VAD, and wakeword pipeline.
**Acceptance Scenarios**:
1. **Given** the satellite is running in passive mode, **When** the user says
   the configured wakeword, **Then** the system plays an acknowledgement
   sound and transitions to active listening within 500 ms.
2. **Given** the satellite is in active listening mode, **When** the user
   speaks a sentence and pauses, **Then** the VAD detects the end of speech
   and the captured PCM audio is delivered to the backend pipeline.
3. **Given** the satellite is in active listening mode, **When** no speech is
   detected for the configured silence timeout, **Then** the system returns
   to passive listening mode and resumes wakeword-only monitoring.
4. **Given** the satellite is in passive mode, **When** the user speaks
   without first saying the wakeword, **Then** the audio is discarded and
   no backend processing occurs.
5. **Given** the wakeword model files are missing or corrupt, **When** the
   satellite starts, **Then** it logs a clear warning and continues in
   a degraded "always active" mode (no wakeword gate).
---
### User Story 2 — Streaming Audio & Interruptible Conversations (Priority: P1)
The user speaks to the satellite after wakeword activation. The captured
audio is streamed to the backend, transcribed to text via speech-to-text,
and routed to the OpenClaw multi-agent framework. The agent's text response
is streamed back sentence-by-sentence through text-to-speech and played as
audio. At any point during assistant playback, the user can begin speaking
(barge-in), which immediately stops the current audio playback, records
the partial utterance already heard by the user, and begins processing the
new input. The assistant's conversation history retains the partial response
for context continuity.
**Why this priority**: Streaming and barge-in are co-equal with wakeword/VAD
as the core interaction loop. Without them, the satellite cannot converse.
**Independent Test**: With a running TTS and gateway backend, the user
speaks a question, receives a spoken response, interrupts mid-sentence, and
receives a new response that demonstrates awareness of the prior partial
answer.
**Acceptance Scenarios**:
1. **Given** the satellite is in active mode and the user has spoken,
   **When** the backend receives the transcribed text, **Then** it streams
   the response to TTS sentence-by-sentence and delivers audio chunks to
   the client with word offset metadata.
2. **Given** the assistant is speaking (playing audio), **When** the user
   begins speaking (barge-in detected by VAD), **Then** the client
   immediately stops all queued audio, reports the word offset of the last
   played word to the backend, and the backend cancels the in-flight
   TTS pipeline.
3. **Given** a barge-in occurred, **When** the new user utterance is
   processed, **Then** the backend injects the partial assistant response
   (up to the interrupted word offset) into conversation history so the
   LLM has accurate context of what the user actually heard.
4. **Given** the backend response takes longer than a configurable threshold
   (default 2 seconds), **When** the threshold elapses, **Then** a
   pre-rendered filler audio clip (e.g., "One moment…") is played to
   maintain conversational flow, and is cancelled if the real response
   arrives before the filler finishes.
5. **Given** the OpenClaw gateway connection is lost during an active
   conversation, **When** the satellite detects the disconnection,
   **Then** it notifies the user via a UI status indicator (never via
   spoken error stack traces), logs the error, and attempts automatic
   reconnection with exponential backoff.
6. **Given** a background agent task completes while the session is passive,
   **When** the result arrives, **Then** the satellite proactively
   reactivates, plays an acknowledgement filler, and speaks a summary of
   the completed task.
---
### User Story 3 — Text-to-Speech with Configurable ONNX Voices (Priority: P2)
The system administrator deploys the satellite with a custom TTS voice by
providing an ONNX model directory, a reference audio WAV file, and a
reference transcript via configuration. At startup, the satellite initializes
the TTS engine with the configured voice character. During conversation, all
assistant responses are synthesized using this voice. Optionally, the
configured voice character may define custom audio post-processing effects
(e.g., glitch distortion for a sci-fi persona) that are applied in real-time
before audio is delivered to the user.
**Why this priority**: TTS is essential for the voice experience but depends
on the streaming pipeline (US2) being functional first. Custom voice support
is what differentiates this from a generic TTS client.
**Independent Test**: An administrator configures a custom voice in
`config.yaml` / `.env`, starts the satellite, and verifies that a
synthesized test phrase plays in the configured voice with any registered
audio effects applied.
**Acceptance Scenarios**:
1. **Given** valid TTS configuration (server URL, character name, ONNX model
   path, reference audio, reference text), **When** the satellite starts,
   **Then** it initializes the TTS character by sending load and reference
   registration requests to the TTS server.
2. **Given** the TTS server is unreachable at startup, **When** the
   satellite starts, **Then** it logs a clear error, continues running
   without TTS (text-only mode), and retries initialization on the first
   synthesis request.
3. **Given** a text response is ready for synthesis, **When** the TTS
   engine processes it, **Then** the text is split into sentences at
   natural boundaries (sentence-ending punctuation and any custom markup
   tags), each sentence is synthesized independently, and PCM audio chunks
   are streamed to the client as they become available.
4. **Given** a voice character with registered audio effects (e.g., pitch
   shift, tremolo, bitcrush, stutter), **When** a sentence is tagged for
   effect processing, **Then** the effects are applied to the raw PCM
   audio before it is delivered to the client.
5. **Given** the TTS engine receives an input that consists only of
   punctuation or non-alphanumeric characters, **When** synthesis is
   attempted, **Then** the engine silently skips the input without
   sending it to the TTS server (preventing hallucinated reference audio).
6. **Given** a synthesis request is in progress, **When** a barge-in or
   cancellation signal arrives, **Then** the TTS pipeline cancels the
   in-flight request and releases resources cleanly.
---
### User Story 4 — Automated Genie TTS Character Setup (Priority: P2)
A user who wants to use a custom TTS voice character downloads a trained
GPT-SoVITS model (`.ckpt` and `.pth` files) plus a reference audio clip
and its text transcription. The current process requires manually running
Python conversion snippets, copying files to the right directories, and
editing `.env` by hand — all undocumented and fragile.

With the automated setup, the user runs a one-time environment script
(`setup_genie.sh`) that creates the virtualenv, installs `genie-tts`, and
creates a staging input folder. They then drop their model assets into that
folder, run `generate_character.py --name <character>`, and the script:
- Validates all required files are present
- Converts the model to ONNX format
- Organises all files into a named character profile directory
- Removes the raw `.ckpt`/`.pth` files from the staging folder (moving them
  into the profile directory for archival), leaving the input folder clean
  for the next character
- Writes all required `GENIE_*` environment variables into `.env` automatically

After running the script, the user starts the Voice Satellite normally and
the configured voice is active immediately.

**Why this priority**: The existing manual process is the primary adoption
barrier. Automating it reduces setup time from 20–40 minutes to under 5
minutes and eliminates the most common class of misconfiguration errors.

**Independent Test**: A user follows the two-script workflow from scratch
with a set of valid model files. They verify the Satellite starts and uses
the correct voice without ever manually editing `.env` or writing Python.

**Acceptance Scenarios**:
1. **Given** `setup_genie.sh` is run, **When** the script completes,
   **Then** a `genie_tts_env/` virtualenv with `genie-tts` installed and
   a `genie_input/` staging directory both exist, and clear instructions
   for the next step are printed.
2. **Given** a `.ckpt`, `.pth`, `.wav`, and `.txt` file are placed in
   `genie_input/` and `generate_character.py --name <name>` is run,
   **When** conversion succeeds, **Then** a `genie_profiles/<name>/`
   directory contains the ONNX export and reference files, and `.env` is
   updated with all `GENIE_*` variables.
3. **Given** conversion completes, **When** checking `genie_input/`,
   **Then** the raw `.ckpt` and `.pth` files have been moved into
   `genie_profiles/<name>/` and the staging folder is empty (ready for
   the next character).
4. **Given** a required file is missing from `genie_input/`,
   **When** `generate_character.py` is run, **Then** it exits with a
   clear error listing exactly which file types are missing, without
   performing any partial conversion.
5. **Given** a character profile already exists at `genie_profiles/<name>/`,
   **When** `generate_character.py --name <name>` is run again,
   **Then** it warns the user and requires an explicit `--overwrite` flag
   to replace the existing profile.
---
### Edge Cases
- What happens when the microphone is disconnected or permission is denied
  mid-session? The system MUST display a clear status message and attempt
  to recover when the device is re-connected.
- What happens when two speech segments arrive in rapid succession before
  the first finishes processing? The system MUST cancel the in-flight
  pipeline for the first segment and begin processing the second.
- What happens when the TTS server returns an HTTP error for a specific
  sentence? The system MUST skip that sentence, log the error, and
  continue with the next sentence in the queue.
- What happens when the WebSocket connection drops during active audio
  playback? The client MUST stop playback, display a disconnection
  indicator, and attempt reconnection.
- What happens when the OpenClaw gateway is unreachable for an extended
  period (hours)? The system MUST continue retrying indefinitely with
  backoff capped at 60 s, enter a degraded-mode UI state where
  wakeword/VAD remain active but agent responses are unavailable, and
  resume normal operation automatically when connectivity is restored.
- What happens when a second client attempts to connect while a session
  is already active? The system MUST reject the new connection with a
  clear error message indicating that a session is already in progress.
## Requirements *(mandatory)*
### Functional Requirements
- **FR-001**: The system MUST support a two-mode listening model: passive
  (wakeword-only monitoring) and active (full VAD speech capture).
- **FR-002**: The system MUST evaluate a user-supplied ONNX wakeword model
  against incoming audio frames in passive mode using a configurable
  confidence threshold.
- **FR-003**: The wakeword detection pipeline MUST be a three-stage process:
  mel-spectrogram extraction → audio embedding → wakeword classification,
  all executed locally on the client device. The pipeline MUST use an
  adaptive threading model: synchronous execution in the VAD callback by
  default, with automatic fallback to a decoupled Web Worker when
  inference timing exceeds a performance threshold (to prevent VAD frame
  drops on constrained hardware).
- **FR-004**: The system MUST use a VAD model to segment speech from silence
  in active mode, with configurable positive/negative speech thresholds,
  pre-speech padding, and minimum speech frame count.
- **FR-005**: All VAD and wakeword inference MUST run locally (on-device)
  without requiring network calls. WASM assets for inference MUST be
  served from the satellite's own static file server — no CDN dependencies.
- **FR-006**: The system MUST stream captured PCM audio to the backend via
  WebSocket as raw 16-bit signed little-endian mono samples at 16 kHz
  (the VAD/STT capture rate boundary).
- **FR-007**: The system MUST support full-duplex WebSocket communication
  between client and backend for simultaneous audio upload and response
  streaming.
- **FR-007a**: The backend MUST enforce a single active WebSocket session
  at a time. If a client connects while a session is already active, the
  backend MUST reject the new connection. When the active session
  disconnects, a new client MAY connect.
- **FR-008**: The backend MUST transcribe incoming PCM audio to text using a
  configurable speech-to-text model (defaulting to a lightweight CPU-only
  model).
- **FR-009**: The backend MUST stream assistant text responses
  sentence-by-sentence to a TTS engine and deliver synthesized PCM audio
  chunks to the client at 32 kHz (the TTS output rate boundary) with
  word offset metadata for barge-in tracking.
- **FR-010**: The system MUST support barge-in: when the user speaks during
  assistant audio playback, the client MUST immediately stop all queued
  audio, report the playback position, and the backend MUST cancel the
  in-flight TTS pipeline.
- **FR-011**: On barge-in, the backend MUST implement a downstream context tracking strategy:
  1. A `TeardownInterceptor` MUST buffer the active assistant text response.
  2. Upon receiving an interruption signal (e.g., `UserStartedSpeakingFrame`), the interceptor MUST immediately commit the buffered partial text to the `TranscriptManager` appended with `... [interrupted]`.
  3. This ensures the Local LLM retains awareness of what the user actually heard before interrupting, preventing redundant or looping responses.
- **FR-012**: The system MUST support pre-rendered filler audio that plays
  automatically when backend response latency exceeds a configurable
  threshold (default 2 seconds), categorized by purpose (thinking,
  working, acknowledging, sign-off).
- **FR-013**: The system MUST connect to the Hermes Agent gateway using a
  configurable URL and bearer token. The Voice Adapter MUST use a Local LLM
  as its primary conversational router to evaluate all incoming user transcripts.
- **FR-014**: The Local LLM MUST natively handle conversational queries. When
  complex tasks or integrations are required, the Local LLM MUST invoke the
  Hermes Agent asynchronously via a tool call (e.g., `query_hermes_agent`).
  This tool dispatch MUST NOT block the Local LLM, allowing it to instantly
  generate a conversational acknowledgment while a background task handles the
  Hermes HTTP response and injects the result into the downstream TTS pipeline.
- **FR-015**: The system MUST support configurable TTS voice characters via
  ONNX model files, reference audio, and reference text — all specified
  through configuration, never hardcoded.
- **FR-016**: The system MUST support optional per-character audio
  post-processing effects (pitch shift, tremolo, overdrive, bitcrush,
  stutter) applied to tagged segments of the synthesized audio.
- **FR-017**: The TTS sentence splitter MUST handle both standard sentence
  boundaries (. ! ?) and custom markup tags (e.g., `<glitch>...</glitch>`)
  to ensure tagged segments are synthesized and processed independently.
- **FR-018**: The system MUST sanitize TTS input: skip inputs with no
  alphanumeric content, ensure trailing punctuation, and handle
  casing requirements for specific voice characters.
- **FR-019**: The Local LLM MUST have access to a `terminate_session` tool. When the user says goodbye or indicates they want to end the conversation, the Local LLM MUST invoke this tool, which emits a status frame to transition the satellite back to a passive wakeword-awaiting state.
- **FR-020**: The system MUST implement a configurable silence timeout
  (default 30 s, range 10–120 s) that returns the satellite from active
  to passive mode after a period of inactivity.
- **FR-021**: All configuration (service URLs, model paths, tokens, audio
  device settings, thresholds) MUST be loaded from a central `.env` file,
  with a documented `.env.example` template.
- **FR-021**: The system MUST fail fast with clear error messages when
  required configuration values are missing, and MUST NOT silently fall
  back to hardcoded defaults for security-sensitive values (tokens).
- **FR-033**: The target OpenClaw agent identifier MUST be configurable via
  a single `OPENCLAW_AGENT_ID` environment variable (default: `main`). All
  user transcripts MUST be routed to this single configured agent. No
  client-side message routing or prefix-parsing logic is permitted.
- **FR-028**: A one-time setup script (`scripts/setup_genie.sh`) MUST
  create a dedicated Python virtualenv for Genie TTS, install the
  `genie-tts` package into it, create a `genie_input/` staging directory
  with a descriptive README, and print clear next-step instructions —
  without requiring any manual virtualenv activation or Python invocation
  from the user.
- **FR-029**: A character generation script (`scripts/generate_character.py`)
  MUST scan `genie_input/` for exactly one `*.ckpt`, one `*.pth`, one
  `*.wav`, and one `*.txt` file. If any required file type is missing or
  more than one of the same type is present, the script MUST exit with a
  clear, actionable error message before performing any conversion.
- **FR-030**: The character generation script MUST perform ONNX model
  conversion using the `genie_tts_env/` virtualenv (created by the setup
  script), write the exported ONNX assets to
  `genie_profiles/<character_name>/export/`, and copy the reference audio
  (`.wav`) and transcript (`.txt`) files into `genie_profiles/<character_name>/`.
- **FR-031**: After a successful ONNX export, the character generation
  script MUST move all source model files (`.ckpt`, `.pth`, and any
  other non-reference files) from `genie_input/` into
  `genie_profiles/<character_name>/` for archival, leaving `genie_input/`
  empty and ready for the next character's assets.
- **FR-032**: The character generation script MUST automatically write or
  patch the `.env` file at the repository root with the `GENIE_TTS_URL`,
  `GENIE_CHARACTER_NAME`, `GENIE_ONNX_MODEL_DIR`, `GENIE_REFERENCE_AUDIO`,
  `GENIE_REFERENCE_TEXT`, and `GENIE_LANGUAGE` variables, deriving all
  paths from the generated profile directory. Existing non-Genie `.env`
  entries MUST be preserved unchanged. If a character profile already
  exists at the target path, the script MUST require an explicit
  `--overwrite` flag before replacing it.
- **FR-022**: The system MUST listen for asynchronous message or notification
  events on the persistent OpenClaw gateway connection. When a task
  completion notification or proactive agent message is received while
  the satellite is in passive mode, the satellite MUST automatically
  reactivate, play an acknowledgment filler, and speak the summary.
- **FR-023**: The system MUST expose a conversation state machine with
  well-defined states (passive_listening, acknowledging, active_listening,
  transcribing, thinking, filler_speaking, speaking, interrupted) and
  emit state transition events over the WebSocket for UI synchronization.
- **FR-024**: The system MUST perform auto-calibration of the ambient noise
  floor from the first few seconds of silence after startup, using the
  result to improve VAD accuracy.
- **FR-025**: The system MUST operate with a dual-rate audio boundary:
  16 kHz for microphone capture, VAD, and STT ingestion; 32 kHz for TTS
  output and client-side audio playback. No implicit resampling is
  permitted — each pipeline stage MUST declare its expected sample rate,
  and the client's audio playback context MUST be initialized at the TTS
  output rate (32 kHz).
- **FR-026**: The wakeword pipeline MUST measure its own inference time on
  startup. If the average three-stage inference exceeds a configurable
  threshold (e.g., 30 ms per frame), the pipeline MUST automatically
  offload to a Web Worker and accept graceful frame-dropping rather than
  blocking the VAD processing loop.
- **FR-027**: When the OpenClaw gateway or TTS server is unreachable, the
  system MUST enter a degraded-mode state: wakeword detection and VAD
  MUST continue operating, the UI MUST display a clear connectivity
  status indicator, and the system MUST retry connection using
  exponential backoff capped at 60 seconds with unlimited retries.
  Normal operation MUST resume automatically upon reconnection.
### Key Entities
- **ConversationSession**: Per-connection state machine tracking the current
  mode, active generation task, silence timer, and barge-in word offsets.
- **WakewordPipeline**: Three-stage ONNX inference chain
  (melspec → embedding → classifier) that evaluates audio frames against
  a configurable wakeword model.
- **AudioChunk**: A unit of PCM audio with metadata (sample rate, format,
  word offset, effect tags) flowing between pipeline stages.
- **FillerBank**: A categorized collection of pre-rendered PCM audio clips
  loaded at startup and served from memory with zero synthesis latency.
- **GatewayConnection**: A persistent WebSocket connection to the OpenClaw
  gateway with challenge-response authentication, session management,
  and streaming text extraction.
- **TTSVoiceCharacter**: A configured voice identity comprising an ONNX
  model, reference audio, reference transcript, and optional audio effect
  chain.
## Success Criteria *(mandatory)*
### Measurable Outcomes
- **SC-001**: A new contributor can clone the repository, configure `.env`,
  and have the satellite running with a working wakeword + VAD pipeline
  within 10 minutes using only the README quickstart.
- **SC-002**: Wakeword detection latency (utterance end to acknowledgement
  audio start) MUST be under 500 ms on a Raspberry Pi 4.
- **SC-003**: End-to-end voice interaction latency (speech end to first
  audio response chunk) MUST be under 3 seconds when backend services
  are local.
- **SC-004**: Barge-in response time (user speech detected to audio
  playback stopped) MUST be under 200 ms on the client side.
- **SC-005**: The satellite MUST run continuously for 24+ hours without
  memory leaks, task accumulation, or degraded responsiveness.
- **SC-006**: All configured filler audio clips MUST begin playback
  within 50 ms of the latency threshold being exceeded (RAM-served,
  no disk or network I/O at playback time).
- **SC-007**: The satellite MUST recover automatically from a gateway
  disconnection using exponential backoff (capped at 60 seconds) with
  unlimited retries. During prolonged outages the satellite MUST enter
  a degraded-mode UI state and resume normal operation without user
  intervention when the gateway becomes reachable.
- **SC-008**: The system MUST deploy identically on x86_64 (laptop) and
  aarch64 (Raspberry Pi) from the same codebase, without any
  source code modifications — configuration-only differences.
## Clarifications
### Session 2026-05-19
- Q: Should the system use a single canonical audio sample rate or
  explicit dual-rate boundaries? → A: Dual-rate with explicit boundaries:
  16 kHz for capture/VAD/STT, 32 kHz for TTS output/client playback.
  No implicit resampling; each stage declares its rate.
- Q: Should the wakeword pipeline run synchronously in the VAD callback
  or be decoupled into a Web Worker? → A: Adaptive — synchronous by
  default, auto-fallback to Web Worker on slow hardware detected via
  inference timing heuristic. Ensures VAD never stalls on constrained
  devices (Raspberry Pi).
- Q: What should happen when the gateway remains unreachable beyond
  the initial reconnection window? → A: Unlimited retries with backoff
  capped at 60 s. Enter degraded-mode UI state where wakeword/VAD
  continue but agent responses are unavailable. Resume automatically
  when connectivity is restored.
- Q: Should the satellite support multiple concurrent WebSocket clients
  or enforce single-client operation? → A: Single active session.
  Additional connections are rejected. The satellite is a physical
  ambient device with one microphone — concurrent multi-user voice
  interaction is not supported.
- Q: Should the gateway client hard-pin to a single protocol version or
  negotiate a range? → A: Negotiate a configurable min/max range (e.g.,
  minProtocol: 3, maxProtocol: 4) for forward compatibility. The
  supported range MUST be configurable so version bumps don't require
  code changes.
## Assumptions
- Users have a working microphone and audio output device accessible
  to the host operating system (ALSA, PulseAudio, or PipeWire).
- The OpenClaw gateway server is deployed and reachable over the network
  (LAN or tunnel). The satellite does not embed the gateway.
- A Genie TTS server (GPT-SoVITS or compatible) is deployed separately.
  The satellite is a client; it does not embed the TTS engine. The
  `scripts/setup_genie.sh` and `scripts/generate_character.py` scripts
  handle the Genie TTS environment and character profile creation.
- The wakeword ONNX model is provided by the user (e.g., trained via
  OpenWakeWord). The satellite does not include a default wakeword model.
- The STT model (e.g., faster-whisper tiny.en) is downloaded automatically
  on first run. No pre-bundled model files are included in the repository.
- The frontend runs in a modern browser (Chrome 90+, Firefox 90+, Edge 90+)
  with WebAssembly SIMD support.
- Network connectivity is intermittent but generally available. The
  satellite MUST handle temporary disconnections gracefully.
- Users have `bash` available on the host system for running setup scripts.
  Python 3.11+ is required to run `generate_character.py`.
- The `genie_input/` staging directory and `genie_profiles/` directory are
  excluded from version control (they contain large binary model files).