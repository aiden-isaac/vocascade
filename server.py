import json
import logging
import asyncio
import aiohttp
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import numpy as np
import openai
from dotenv import load_dotenv
import os
import base64
import tempfile
from faster_whisper import WhisperModel

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI()

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def get():
    with open("static/index.html", "r") as f:
        return HTMLResponse(f.read())

LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "dummy_key")
LITELLM_URL = "https://llm.frizzt.com/v1"
LLM_MODEL = "qwen-moe-coder-fast"

# Async OpenAI client
client = openai.AsyncOpenAI(
    api_key=LITELLM_API_KEY,
    base_url=LITELLM_URL
)

# Load Faster Whisper Model
# Using tiny.en or base.en for speed
logger.info("Loading Whisper model...")
# Ensure compute_type is float32 to avoid issues on systems without float16 support (like CPUs without it)
whisper_model = WhisperModel("tiny.en", device="cpu", compute_type="float32")
logger.info("Whisper model loaded.")

async def transcribe_audio(audio_data: bytes) -> str:
    """Transcribes PCM audio using faster-whisper."""
    logger.info(f"Received audio length: {len(audio_data)}")
    
    # Assume JS sends 16kHz 16-bit PCM (Int16)
    audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
    
    # Run transcription in a thread to avoid blocking the event loop
    def run_whisper():
        segments, info = whisper_model.transcribe(audio_np, beam_size=1)
        text = " ".join([segment.text for segment in segments])
        return text
    
    transcribed_text = await asyncio.to_thread(run_whisper)
    return transcribed_text.strip()

async def genie_tts_complete(text: str) -> bytes:
    """Calls the local genie-tts GPT-SoVITS API and returns the full WAV file as bytes."""
    logger.info(f"Generating TTS for: {text}")
    url = "http://127.0.0.1:9880/"
    params = {"text": text, "text_language": "en"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    logger.error(f"TTS API error: {response.status} - {await response.text()}")
                    return b''
                wav_bytes = await response.read()
                if not wav_bytes:
                    logger.error("TTS API returned empty bytes.")
                    return b''
                logger.info(f"TTS API returned {len(wav_bytes)} bytes for text: {text[:20]}...")
                return wav_bytes
    except Exception as e:
        logger.error(f"Failed to connect to TTS API: {e}")
        return b''

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        # State per connection
        self.connection_states = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        self.connection_states[websocket] = {
            "interrupt_event": asyncio.Event(),
            "llm_task": None,
            "tts_tasks": set(),
            "is_speaking": False
        }

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        state = self.connection_states.pop(websocket, None)
        if state:
            self._cancel_tasks(state)

    def _cancel_tasks(self, state):
        state["interrupt_event"].set()
        if state["llm_task"]:
            state["llm_task"].cancel()
        for task in state["tts_tasks"]:
            task.cancel()
        state["tts_tasks"].clear()
        state["is_speaking"] = False

    async def process_audio(self, websocket: WebSocket, audio_data: bytes):
        state = self.connection_states[websocket]
        
        # 1. Run STT
        try:
            transcribed_text = await transcribe_audio(audio_data)
            logger.info(f"Transcribed: {transcribed_text}")
            if not transcribed_text.strip():
                return
        except Exception as e:
            logger.error(f"STT Error: {e}")
            return

        # 2. Reset state for new interaction
        self._cancel_tasks(state)
        state["interrupt_event"].clear()
        state["is_speaking"] = True

        # 3. Start LLM Generation
        state["llm_task"] = asyncio.create_task(
            self._run_llm_and_tts(websocket, transcribed_text, state)
        )

    async def _run_llm_and_tts(self, websocket: WebSocket, prompt: str, state: dict):
        try:
            response = await client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": "You are a concise voice assistant."},
                    {"role": "user", "content": prompt}
                ],
                stream=True
            )

            current_sentence = ""
            # Simple sentence splitting logic
            sentence_ends = {'.', '!', '?'}

            async for chunk in response:
                if state["interrupt_event"].is_set():
                    logger.info("LLM Generation interrupted.")
                    break

                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    current_sentence += content
                    
                    if any(char in sentence_ends for char in content):
                        sentence = current_sentence.strip()
                        if sentence:
                            # Start a TTS task for this sentence
                            tts_task = asyncio.create_task(self._run_tts(websocket, sentence, state))
                            state["tts_tasks"].add(tts_task)
                            # Cleanup callback
                            tts_task.add_done_callback(lambda t: state["tts_tasks"].discard(t))
                            current_sentence = ""

            # Handle remainder
            sentence = current_sentence.strip()
            if sentence and not state["interrupt_event"].is_set():
                tts_task = asyncio.create_task(self._run_tts(websocket, sentence, state))
                state["tts_tasks"].add(tts_task)
                tts_task.add_done_callback(lambda t: state["tts_tasks"].discard(t))

        except asyncio.CancelledError:
            logger.info("LLM task cancelled.")
        except Exception as e:
            logger.error(f"LLM Error: {e}")
        finally:
            self._check_speaking_state(state)

    async def _run_tts(self, websocket: WebSocket, text: str, state: dict):
        try:
            wav_bytes = await genie_tts_complete(text)
            if not wav_bytes:
                logger.error(f"_run_tts: No wav_bytes returned for {text[:20]}...")
                return
            
            # Send the full WAV as base64 in one shot
            b64_data = base64.b64encode(wav_bytes).decode('utf-8')
            logger.info(f"_run_tts: Sending base64 audio data length: {len(b64_data)}")
            await websocket.send_json({
                "type": "audio",
                "data": b64_data
            })
        except asyncio.CancelledError:
            logger.info(f"TTS task cancelled for: {text[:20]}...")
        except Exception as e:
            logger.error(f"TTS Error: {e}")
        finally:
            self._check_speaking_state(state)

    def _check_speaking_state(self, state):
        if not state["tts_tasks"] and not (state["llm_task"] and not state["llm_task"].done()):
            state["is_speaking"] = False

    async def handle_interrupt(self, websocket: WebSocket):
        state = self.connection_states.get(websocket)
        if state and state.get("is_speaking", False):
            logger.info("Interrupt received from client. Gating allowed (backend is speaking). Cancelling tasks.")
            self._cancel_tasks(state)
        else:
            logger.info("Interrupt received from client but ignored (backend state is listening).")
            
manager = ConnectionManager()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # We expect binary data for audio, or text for json commands
            data = await websocket.receive()
            if data.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(data.get("code", 1000))
            if "bytes" in data:
                audio_bytes = data["bytes"]
                logger.info(f"Received audio packet via bytes: {len(audio_bytes)} bytes")
                await manager.process_audio(websocket, audio_bytes)
            elif "text" in data:
                try:
                    message = json.loads(data["text"])
                    msg_type = message.get("type")
                    
                    if msg_type == "interrupt":
                        await manager.handle_interrupt(websocket)
                    elif msg_type == "audio_data":
                        # If base64 encoded
                        audio_b64 = message.get("data", "")
                        audio_bytes = base64.b64decode(audio_b64)
                        logger.info(f"Received audio packet via JSON b64: {len(audio_bytes)} bytes")
                        await manager.process_audio(websocket, audio_bytes)
                        
                except json.JSONDecodeError:
                    logger.warning("Received invalid JSON")
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket Error: {e}")
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
