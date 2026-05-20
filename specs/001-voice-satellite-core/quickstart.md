# Quickstart: Voice Satellite
Get the Voice Satellite running in under 10 minutes.
## Prerequisites
- **Python 3.11+** with `pip`
- **Linux** (x86_64 or aarch64 — tested on Ubuntu 22.04+ and Raspberry Pi OS)
- A **microphone** and **speakers/headphones** (or USB audio device)
- A modern **browser** (Chrome 90+, Firefox 90+, Edge 90+)
- A running **Genie TTS server** (see [Genie TTS Setup](#genie-tts-server))
- Network access to an **OpenClaw gateway** (or local tunnel)
## 1. Clone & Install
```bash
git clone https://github.com/openclaw/voice-satellite.git
cd voice-satellite
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
## 2. Configure
```bash
cp .env.example .env
```
Edit `.env` with your values. **Required** fields (satellite will not start without these):
```ini
LITELLM_API_KEY=your_litellm_api_key
OPENCLAW_GATEWAY_TOKEN=your_openclaw_token
```
**Required for voice** (satellite runs in text-only degraded mode without these):
```ini
GENIE_TTS_URL=http://127.0.0.1:8000
GENIE_CHARACTER_NAME=ordis
GENIE_ONNX_MODEL_DIR=/path/to/genie-ordis/export
GENIE_REFERENCE_AUDIO=/path/to/ordis_ref.wav
GENIE_REFERENCE_TEXT=What? A parity drift? How is that possible?
```
All other values have sensible defaults. See `.env.example` for the full list.
## 3. Add a Wakeword Model
Place your OpenWakeWord ONNX models in `static/wakeword/`:
```
static/wakeword/
├── melspectrogram.onnx      # Mel-spectrogram model
├── embedding_model.onnx     # Audio embedding model
├── model.onnx               # Your trained wakeword classifier
└── model.json               # Model metadata
```
Example `model.json`:
```json
{
  "file": "model.onnx",
  "name": "Hey Ordis",
  "sample_rate": 16000,
  "threshold": 0.5
}
```
> **No wakeword model?** The satellite will run in "always active" mode
> (no wakeword gate). You can train a custom wakeword model using
> [OpenWakeWord](https://github.com/dscripka/openWakeWord).
## 4. Generate Filler Audio (Optional)
If your Genie TTS server is running, pre-render filler audio clips:
```bash
python scripts/generate_fillers.py
```
This populates `static/fillers/` with short PCM phrases like "One moment…"
and "Let me think…" that play during response latency.
## 5. Start the Satellite
```bash
python -m voice_satellite
```
You should see the startup health report:
```
╔══════════════════════════════════════════════════╗
║  Voice Satellite — Startup Health Report         ║
╠══════════════════════════════════════════════════╣
║  Config:     .env loaded ✓                       ║
║  STT:        tiny.en (CPU) ✓                     ║
║  TTS:        ordis @ http://127.0.0.1:8000 ✓     ║
║  Gateway:    ws://127.0.0.1:18789 (v3) ✓         ║
║  Fillers:    14 loaded (5 categories) ✓          ║
║  Wakeword:   model.onnx (Hey Ordis) [frontend]   ║
║  Listening:  http://0.0.0.0:8000                 ║
╚══════════════════════════════════════════════════╝
```
## 6. Open the UI
Navigate to **http://localhost:8000** in your browser.
1. Click **Start Listening**
2. Grant microphone permission when prompted
3. Wait for "Passive listening — waiting for wakeword"
4. Say your wakeword (e.g., "Hey Ordis")
5. After the acknowledgement sound, speak naturally
6. The assistant will respond through your speakers

## Genie TTS Server Setup
The Voice Satellite leverages **Genie TTS** (a high-performance wrapper around GPT-SoVITS) to clone voices locally. Because of its large PyTorch/CUDA dependencies, it is recommended to run the Genie TTS server in a separate Python virtual environment.

### 1. Prerequisites & Environment
1. Create a dedicated directory or sub-folder for your Genie setup (e.g., `genie_model_reference/`).
2. Set up and activate a separate virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install genie-tts
   ```

### 2. Prepare Model Assets
Genie TTS requires a trained GPT-SoVITS voice model. Place the following files together in your Genie directory:
- `*.ckpt` (SoVITS weights)
- `*.pth` (GPT weights)
- `*.wav` (Reference audio file representing the voice to clone, ~5-10 seconds)
- `*.txt` (The exact text transcription of the reference audio)

### 3. Export to ONNX
For low-latency CPU/GPU execution, convert the PyTorch weights to ONNX format. Use the conversion utility:
```python
from genie_tts.Converter.Converter import convert

convert(
    torch_ckpt_path="path/to/model.ckpt",
    torch_pth_path="path/to/model.pth",
    output_dir="path/to/export"
)
```
This generates optimized ONNX model assets inside the `export/` directory.

### 4. Running the Server
Ensure that the `GenieData/` resource folder (containing foundational helper models like `speaker_encoder.onnx`) is located inside your Genie directory. Start the Genie TTS HTTP server on port `8000`:
```python
import genie_tts as genie
import os

# Point genie to the resources directory
os.environ["GENIE_DATA_DIR"] = "/path/to/GenieData"

genie.start_server(host="0.0.0.0", port=8000, workers=1)
```

### 5. Client Configuration
Once the server is running on port `8000`, configure your Voice Satellite client by updating your `.env` file with the paths to the exported ONNX model and the reference audio:
```ini
GENIE_TTS_URL=http://127.0.0.1:8000
GENIE_CHARACTER_NAME=ordis
GENIE_ONNX_MODEL_DIR=/path/to/genie-ordis/export
GENIE_REFERENCE_AUDIO=/path/to/ordis_ref.wav
GENIE_REFERENCE_TEXT=What? A parity drift? How is that possible?
GENIE_LANGUAGE=en
```

> [!NOTE]
> If your Genie server is unreachable or configuration is incomplete, the Voice Satellite client automatically falls back to a **degraded mode** (text-only responses inside the browser UI, without voice outputs), ensuring overall system functionality remains intact.

## Troubleshooting
| Symptom | Cause | Fix |
|---------|-------|-----|
| "LITELLM_API_KEY is required" | Missing `.env` value | Add your API key to `.env` |
| VAD never detects speech | Browser mic permission blocked | Check browser permissions |
| No audio playback | AudioContext suspended | Click the UI first (browser autoplay policy) |
| TTS WARNING at startup | Genie TTS server not running | Start the TTS server first |
| "Session already active" | Another browser tab connected | Close other tabs |

## Raspberry Pi Notes
- Use `WHISPER_MODEL=tiny.en` (default) for acceptable CPU performance
- The wakeword pipeline auto-detects slow hardware and offloads to a Web Worker
- Ensure your audio device is accessible (check `aplay -l` and `arecord -l`)
- For best results, use a USB microphone rather than the 3.5mm jack