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
The Voice Satellite leverages **Genie TTS** (a high-performance wrapper around GPT-SoVITS) to clone voices locally. Because of its large PyTorch/CUDA dependencies, we provide automated setup and generation scripts that handle virtual environment management, model conversion, and client configuration.

### 1. Run Setup Script
Run the setup script to initialize the Genie environment and create the staging directory:
```bash
bash scripts/setup_genie.sh
```
This will:
1. Create a dedicated Python virtual environment (`genie_tts_env/`) at the repository root.
2. Install `genie-tts` and its dependencies inside that virtual environment.
3. Create a `genie_input/` staging folder for raw model assets.

### 2. Stage Model Assets
Place exactly four files from your trained GPT-SoVITS model into the `genie_input/` directory:
- Exactly one `*.ckpt` (SoVITS model checkpoint)
- Exactly one `*.pth` (GPT model weights)
- Exactly one `*.wav` (Reference audio file, ~5-10 seconds)
- Exactly one `*.txt` (The exact text transcription of the reference audio)

### 3. Generate Character Profile
With your assets staged in `genie_input/`, run the character generation script from the repository root:
```bash
python scripts/generate_character.py --name <character_name>
```
The script will automate the remaining steps:
1. **Validation**: Check that exactly one of each required file type exists.
2. **ONNX Export**: Convert the model to optimized ONNX assets and output them to `genie_profiles/<character_name>/export/`.
3. **Archival & Staging Cleanup**: Move the raw source files (`.ckpt`, `.pth`, `.wav`, `.txt`) from `genie_input/` into the new profile folder at `genie_profiles/<character_name>/` for archival, leaving `genie_input/` clean and ready for the next character.
4. **Client Auto-Patching**: Automatically append or update the `GENIE_*` environment variables in your `.env` file using absolute paths to the generated profile.

### 4. Overwriting or Re-generating Profiles
If a profile directory already exists under the target name, the generation script will fail safely to protect your files. If you explicitly want to recreate the profile and overwrite it, use the `--overwrite` flag:
```bash
python scripts/generate_character.py --name <character_name> --overwrite
```

> [!NOTE]
> If your Genie server is unreachable or configuration is incomplete, the Voice Satellite client automatically falls back to a **degraded mode** (text-only responses inside the browser UI, without voice outputs), ensuring overall system functionality remains intact.

## Troubleshooting
| Symptom | Cause | Fix |
|---------|-------|-----|
| "OPENCLAW_GATEWAY_TOKEN is required" | Missing `.env` value | Add your gateway token to `.env` |
| "Invalid files in genie_input/" | Missing or extra model files during character generation | Ensure exactly one `.ckpt`, `.pth`, `.wav`, and `.txt` exist in `genie_input/` |
| VAD never detects speech | Browser mic permission blocked | Check browser permissions |
| No audio playback | AudioContext suspended | Click the UI first (browser autoplay policy) |
| TTS WARNING at startup | Genie TTS server not running | Start the TTS server first |
| "Session already active" | Another browser tab connected | Close other tabs |

## Raspberry Pi Notes
- Use `WHISPER_MODEL=tiny.en` (default) for acceptable CPU performance
- The wakeword pipeline auto-detects slow hardware and offloads to a Web Worker
- Ensure your audio device is accessible (check `aplay -l` and `arecord -l`)
- For best results, use a USB microphone rather than the 3.5mm jack