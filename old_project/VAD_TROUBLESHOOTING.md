# Voice Satellite VAD Troubleshooting Report

## Problem Statement

The Silero VAD in the web frontend fails to detect speech. Every audio frame produces:

```
Error: input 'state' is missing in 'feeds'.
    at _InferenceSession.run (inference-session-impl.ts:97:15)
    at t.FrameProcessor.process [as modelProcessFunc] (vad.bundle.min.js:1:5300)
```

This error repeats hundreds of times per second. No `onSpeechStart`, `onSpeechEnd`, or `onVADMisfire` callbacks ever fire. The VAD runs continuously but detects nothing.

---

## Current Architecture

### Project Structure
```
~/voice-satellite/
├── server.py              # FastAPI orchestrator (port 8000)
├── voice_satellite/
│   ├── genie_tts.py       # TTS client (port 9880)
│   ├── llm_router.py      # LLM routing (LiteLLM)
│   └── openclaw_gateway.py # OpenClaw WebSocket client
├── static/
│   ├── index.html         # Single-page frontend
│   └── libs/              # Vendored WASM/VAD assets
├── genie_model_reference/ # Genie TTS models (loaded at startup)
├── .env                   # Secrets (OPENCLAW_GATEWAY_TOKEN, LITELLM_API_KEY)
├── start_servers.sh       # Starts both servers
├── stop_servers.sh        # Stops both servers
└── server.log             # Server-side logs
```

### Data Flow
1. **Frontend**: Browser loads `index.html`, connects to server via WebSocket at `/ws`
2. **VAD**: `@ricky0123/vad-web` MicVAD runs in-browser, monitors microphone
3. **Audio**: When VAD detects speech end, frontend sends raw PCM bytes over WebSocket binary
4. **Backend**: `server.py` receives PCM → runs `faster-whisper` STT → calls LiteLLM → calls OpenClaw gateway → TTS via Genie → sends base64 PCM back to frontend

### Port Map
| Service | Port | Notes |
|---------|------|-------|
| FastAPI (server.py) | 8000 | Main orchestrator |
| Genie TTS | 9880 | GPT-SoVITS service |
| OpenClaw Gateway | 18789 | LAN: `192.168.8.104:18789`, Tunnel: `127.0.0.1:18789` |
| LiteLLM | `llm.frizzt.com` | Remote LLM endpoint |

### Key Code Files
- **Frontend VAD init**: `static/index.html` lines 249–292
- **Backend WebSocket handler**: `server.py` `websocket_endpoint()`
- **STT**: `server.py` `transcribe_audio()` — uses `faster-whisper` (CPU, `tiny.en`)
- **TTS**: `voice_satellite/genie_tts.py` — connects to `http://127.0.0.1:9880/`
- **OpenClaw Gateway**: `voice_satellite/openclaw_gateway.py` — WebSocket client, protocol v3

---

## What Has Been Tried

### Attempt 1: Lower VAD Sensitivity Thresholds
Changed `positiveSpeechThreshold` from 0.5 to 0.4, `negativeSpeechThreshold` from 0.35 to 0.25, `redemptionFrames` from 35 to 10.

**Result**: No effect. The error persists identically. The VAD never reaches the point of evaluating thresholds — it crashes on every frame before any callback fires.

### Attempt 2: Switch from v5 to Legacy Model
Changed `model: "v5"` to `model: "legacy"` in `static/index.html` line 250.

**Result**: No effect. The error persists with identical stack trace.

### Attempt 3: Symlink Change
Created `static/libs/silero_vad.onnx -> silero_vad_legacy.onnx` (was pointing to v5).

**Result**: No effect. The error persists. The symlink was only fixing a 404 for the ONNX file URL — it had no bearing on the `state` missing error.

### Attempt 4: Add Diagnostic Logging
Added `onFrameProcessed` callback and `audioContext.resume()` call.

**Result**: No effect. The error persists.

---

## Root Cause Analysis

### The Core Bug
The Silero VAD v5 ONNX model has **different input tensor names** than the v4/legacy model. The v5 model requires hidden state inputs (`state`, `state_c`) that the VAD library's `FrameProcessor` does not provide.

From the browser console, the error is definitive:
```
Error: input 'state' is missing in 'feeds'.
```

This is a **known bug** in `@ricky0123/vad-web` — see GitHub issue [#120](https://github.com/ricky0123/vad/issues/120): "Support for version 5". The issue was closed but the v5 ONNX model input names changed and the library's `FrameProcessor` wasn't updated to provide the state tensors.

### Why the Legacy Model Also Fails
Even after switching `model: "legacy"` in the code, the VAD still fails. This is because:

1. The VAD library's `MicVAD.new()` constructor accepts a `model` parameter but internally constructs the ONNX model URL as `silero_vad.onnx` regardless of the model name
2. The bundled `vad.bundle.min.js` v0.0.18-lean has a hardcoded model fetcher that always requests `silero_vad.onnx`
3. The symlink `static/libs/silero_vad.onnx` was originally pointing to the v5 model, causing the v5 model to load even when `model: "legacy"` was set

After changing the symlink to point to the legacy model, the error still occurs because the **bundled VAD library itself** (v0.0.18-lean) has the buggy `FrameProcessor` that doesn't provide state tensors for **any** model version.

### Version Mismatch
| Component | Our Project | open-llm-vtuber-web |
|-----------|-------------|---------------------|
| `@ricky0123/vad-web` | vendored v0.0.18-lean | `^0.0.24` (latest) |
| `onnxruntime-web` | 1.17.0 (vendored) | 1.14.0 |
| VAD model | silero_vad_v5.onnx | silero_vad_v5.onnx |
| **Works?** | **No** | **Yes** |

The open-llm-vtuber-web project uses the same v5 model successfully because their VAD library version (0.0.24+) includes the fix for the state tensor issue.

---

## Reference Project Paths

### open-llm-vtuber-web (working VAD implementation)
```
~/reference/open-llm-vtuber-web/
├── package.json          # @ricky0123/vad-web ^0.0.24, onnxruntime-web 1.14.0
├── src/renderer/
│   ├── src/
│   │   ├── context/
│   │   │   └── vad-context.tsx    # Full VAD context provider (406 lines)
│   │   ├── hooks/
│   │   │   └── utils/
│   │   │       └── use-mic-toggle.ts   # VAD usage pattern
│   │   └── App.tsx              # Wraps VADProvider
│   └── public/                  # Served static assets
└── WebSDK/                    # Additional SDK code
```

Key VAD initialization from `vad-context.tsx` (lines 278-296):
```typescript
const newVAD = await MicVAD.new({
  model: "v5",
  preSpeechPadFrames: 20,
  positiveSpeechThreshold: settings.positiveSpeechThreshold / 100,
  negativeSpeechThreshold: settings.negativeSpeechThreshold / 100,
  redemptionFrames: settings.redemptionFrames,
  baseAssetPath: './libs/',
  onnxWASMBasePath: './libs/',
  onSpeechStart: handleSpeechStart,
  onSpeechRealStart: handleSpeechRealStart,
  onFrameProcessed: handleFrameProcessed,
  onSpeechEnd: handleSpeechEnd,
  onVADMisfire: handleVADMisfire,
});
```

This is nearly identical to our frontend code, confirming the issue is in the **bundled VAD library**, not our initialization code.

### open-llm-vtuber (archived lab, no code)
```
~/reference/open-llm-vtuber/
└── README.md    # Just a redirect notice, no code
```
This is only an archived README — no VAD or audio code to reference.

---

## VAD Library Differences

### Our Bundled VAD (broken)
- File: `static/libs/vad.bundle.min.js`
- Version: `vad-0.0.18-lean` (inline in HTML: `v=vad-0.0.18-lean`)
- Source: Custom vendor/extracted bundle
- Issue: `FrameProcessor.process()` calls `session.run(feeds)` without state tensors
- The `Silero` class in the bundle has `reset_state()` but it's never called before `process()`

### Reference VAD (working)
- Package: `@ricky0123/vad-web` v0.0.24+ (latest: v0.0.30)
- Source: `~/reference/open-llm-vtuber-web/package.json`
- Fix: The newer library properly initializes and passes state tensors to the ONNX session

### New VAD Package Contents (v0.0.30)
```
/tmp/package/dist/
├── bundle.min.js           # Updated VAD bundle
├── silero_vad_v5.onnx      # 2.3MB v5 model
├── silero_vad_legacy.onnx  # 1.8MB legacy model
├── vad.worklet.bundle.min.js
└── models/
    ├── v5.js               # v5-specific model loader
    ├── legacy.js           # legacy-specific model loader
    └── common.js           # shared utilities
```

The v0.0.30 package includes separate `models/v5.js` and `models/legacy.js` modules that handle the different ONNX input shapes — this is the fix that was missing in v0.0.18.

---

## What Needs to Be Done

1. **Replace the bundled VAD library** with the latest `@ricky0123/vad-web` v0.0.30:
   - Replace `static/libs/vad.bundle.min.js` with `/tmp/package/dist/bundle.min.js`
   - Replace `static/libs/vad.worklet.bundle.min.js` with `/tmp/package/dist/vad.worklet.bundle.min.js`
   - Copy `silero_vad_v5.onnx` and `silero_vad_legacy.onnx` to `static/libs/`

2. **Update the HTML import** in `static/index.html` to reference the new bundle:
   - Current: `<script src="/static/libs/vad.bundle.min.js?v=vad-0.0.18-lean"></script>`
   - New: `<script src="/static/libs/vad.bundle.min.js?v=vad-0.0.30"></script>`

3. **Update the frontend VAD initialization** in `static/index.html` to match the working reference:
   - Remove `onnxWASMBasePath` parameter (not needed in newer versions)
   - Ensure `baseAssetPath` is set correctly
   - Consider adding `onSpeechRealStart` callback (available in newer VAD versions for barge-in)

4. **Clean up symlinks**: Remove `static/libs/silero_vad.onnx` symlink — the new VAD library handles model selection internally via the `model` parameter.

5. **Verify** by opening the web UI in a browser and checking the console for:
   - `[VAD] vad is initialized` (no errors after this)
   - `[VAD] initialized worklet`
   - No `input 'state' is missing in 'feeds'` errors
   - Speech events firing correctly in the UI log

---

## Addendum: WASM Fetching Issue (CDN Dependency)

**Issue**: After resolving the ONNX model tensor mismatch, the application threw a `[wasm] TypeError: Failed to fetch` or `[cpu] Error: previous call to 'initializeWebAssembly()' failed` error in the browser console.

**Root Cause**: The `@ricky0123/vad-web` library natively falls back to downloading its core `ort-wasm-simd.wasm` files from `cdn.jsdelivr.net` if the WASM path isn't explicitly provided, regardless of `baseAssetPath`. In environments with ad-blockers, tracking protection, or offline constraints, this request is blocked or times out, preventing the ONNX runtime from initializing.

**Resolution**: Added `onnxWASMBasePath: ASSET_PATH` to the `MicVAD.new()` initialization configuration in `static/index.html`. This explicitly forces the `ort.js` WASM runner to use the local files served by our backend, entirely removing the CDN dependency.

---

## Environment Details

- **Host**: `athrogate` (Linux, CPU-bound inference)
- **Python**: venv at `~/voice-satellite/venv/`
- **Dependencies**: `fastapi`, `uvicorn`, `websockets`, `openai`, `python-dotenv`, `faster-whisper`, `numpy`, `scipy`, `aiohttp`
- **Browser logs**: `~/voice-satellite/renna.frizzt.com-1777960912532.log` (13,188 lines of browser console output)
- **Server logs**: `~/voice-satellite/server.log` (86 lines, shows successful STT/TTS when audio is sent manually)
- **Test scripts**: `test_openclaw_gateway.py`, `test_llm_router.py`, `test_genie_tts.py`, etc. — all run with `python test_*.py` directly (no pytest)
