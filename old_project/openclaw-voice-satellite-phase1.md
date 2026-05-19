# OpenClaw Voice Satellite System: Phase 1 Architecture Document

## 1. System Overview

The Phase 1 Voice Satellite System is a web-based, real-time conversational interface designed to integrate with OpenClaw. It prioritizes low-latency voice interactions with robust interruption (barge-in) capabilities.

**Deployment Constraints:**
*   **Host:** `athrogate` (CPU-bound inference).
*   **Client (Phase 1):** Web-only. Mobile-friendly UI with simple summon/dismiss controls and audio capture/playback. Hardware clients (Raspberry Pi) are deferred to Phase 2+.

## 2. Core Components

### 2.1 Web Client (Frontend)
*   **UI:** Minimalist interface featuring a Summon/Dismiss button.
*   **Audio Capture:** Uses the browser's MediaStream API. Integrates a lightweight Voice Activity Detection (VAD) module (e.g., Silero VAD via WebAssembly or similar web-friendly VAD) to detect user speech locally.
*   **Audio Playback:** Utilizes the Web Audio API (`AudioContext`) for gapless playback of raw PCM streams received from the backend.
*   **Transport:** Maintains a persistent, full-duplex WebSocket connection to the Python Backend.

### 2.2 Python Conversation Service (Backend)
*   **Role:** The central orchestrator running on `athrogate`. Manages the session state, coordinates LLM generation, handles TTS queues, and routes audio.
*   **Session Management:** Centralized state tracking for the active conversation.
*   **Failure Handling:** Hard failures (e.g., LLM timeout, TTS crash, WS disconnects) are caught and routed silently to Aiden via Telegram. The system must *never* speak error stack traces aloud.

### 2.3 LLM & TTS Engines
*   **LLM Engine:** Streaming text generation.
*   **TTS Engine:** `genie-tts` running asynchronously. Optimized for CPU execution on `athrogate`.

## 3. State Machine

The backend maintains a strict state machine per session:

1.  **`idle`:** Waiting for the user to summon the assistant or initiate speech.
2.  **`listening`:** VAD is active, and the backend is receiving audio chunks from the client.
3.  **`transcribing`:** User has stopped speaking; audio is sent to the STT (Speech-to-Text) engine.
4.  **`thinking`:** LLM generation has started. Waiting for the first sentence boundary to be detected.
5.  **`speaking`:** `genie-tts` is actively generating PCM audio, and the backend is streaming it to the web client via WebSockets.
6.  **`interrupted`:** A barge-in event occurred. Immediate cleanup of queues and buffers before returning to `listening`.

## 4. Streaming Mechanics & Data Flow

### 4.1 Chunking & TTS Pipeline
*   The LLM generates text as a stream.
*   The backend processes the incoming token stream and applies sentence boundary detection (e.g., splitting on `.`, `?`, `!`, or logical pauses).
*   Once a complete sentence is formed, it is immediately pushed into an asynchronous queue for `genie-tts`.
*   `genie-tts` pulls from this queue, synthesizes the audio, and yields PCM data.

### 4.2 WebSocket Protocol & Delivery
*   **Connection:** Persistent WebSocket.
*   **Audio Payload (Backend -> Client):** The backend streams generated audio as raw PCM or Base64-encoded PCM wrapped in JSON:
    ```json
    {
      "type": "audio",
      "format": "pcm_16khz_16bit_mono",
      "data": "<base64_string>"
    }
    ```
*   **Playback:** The web client decodes the payload, feeds it into an `AudioBuffer`, and schedules it within the `AudioContext` time domain to ensure seamless, gapless playback across sentence chunks.

### 4.3 Interruption (Barge-in) Flow
Barge-in is a mandatory, hard requirement. Latency here must be minimal.

1.  **Detection:** While the system is in the `speaking` state, the client-side VAD detects the user beginning to speak.
2.  **Signal:** The web client instantly fires an interrupt message over the WebSocket:
    ```json
    { "type": "interrupt" }
    ```
3.  **Client-Side Action:** The frontend immediately flushes its `AudioContext` buffers, silencing the output instantly.
4.  **Backend-Side Action:** Upon receiving the `interrupt` signal, the backend:
    *   Transitions state to `interrupted`.
    *   Sends a cancellation token to halt the active LLM text generation stream.
    *   Terminates the currently running `genie-tts` inference task.
    *   Purges any pending sentences in the TTS async queue.
    *   Transitions state back to `listening` to process the new user input.

## 5. Telemetry and Error Routing
*   Standard logs are written to stdout/file.
*   Exceptions in the Python Conversation Service trigger a lightweight Telegram notification payload sent silently to Aiden's designated chat ID. No audio failure messages will be synthesized.
