"""
server.py -- FastAPI WebSocket orchestrator for the Voice Satellite.

Entry point for:
  - Silero VAD speech ingestion (binary PCM from browser)
  - Wakeword and barge-in handling via ConversationSession
  - Whisper STT transcription (faster-whisper, CPU, INT8)
  - LLM routing (LLMRouter -> Qwen via LiteLLM)
  - OpenClaw background task management (TaskTracker)
  - Filler audio injection (FillerEngine, 2s timer race)
  - Genie TTS synthesis + Ordis glitch distortion
  - WebSocket audio streaming to the browser

Run via: ./start_servers.sh  (port 8001)
"""

import asyncio
import json
import logging
import os
import random
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import numpy as np
from dotenv import load_dotenv
from faster_whisper import WhisperModel
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from voice_satellite.filler_engine import FillerEngine
from voice_satellite.genie_tts import (
    GENIE_SAMPLE_RATE,
    GenieTTSClient,
    encode_pcm_chunk,
    iter_complete_sentences,
)
from voice_satellite.llm_router import CoordinatorDecision, LLMRouter, RouterDecision
from voice_satellite.openclaw_gateway import OpenClawGatewayClient
from voice_satellite.session import ConversationSession, SessionState
from voice_satellite.task_tracker import TaskTracker, TrackedTask


def apply_ordis_glitch(pcm_bytes: bytes) -> bytes:
    if not pcm_bytes:
        return pcm_bytes

    # Needs to be a multiple of 2 bytes for 16-bit PCM
    if len(pcm_bytes) % 2 != 0:
        pcm_bytes = pcm_bytes[:-1]

    arr = np.frombuffer(pcm_bytes, dtype=np.int16).copy()

    # Dynamic randomized parameters
    current_pitch = round(random.uniform(0.55, 0.63), 3)
    current_tremolo = round(random.uniform(8.0, 14.0), 1)
    current_overdrive = round(random.uniform(2.5, 4.5), 1)
    current_bitcrush = random.randint(1, 4)
    current_stutter_ms = random.randint(70, 120)
    current_stutter_count = random.randint(2, 4)
    current_downsample = 2 if random.random() < 0.10 else 1

    # 0. Pitch Shift (Resampling)
    indices = np.arange(0, len(arr), current_pitch)
    floor = np.floor(indices).astype(int)
    ceil = np.minimum(floor + 1, len(arr) - 1)
    weight = indices - floor
    arr = (arr[floor] * (1 - weight) + arr[ceil] * weight).astype(np.int16)

    # 0.5 Tremolo / Ring Modulation (Growl effect)
    t = np.arange(len(arr)) / 32000.0
    mod = np.sin(2 * np.pi * current_tremolo * t)
    arr = (arr * (0.5 + 0.5 * mod)).astype(np.int16)

    # 1. Overdrive/Clipping
    arr_32 = arr.astype(np.int32) * current_overdrive
    arr = np.clip(arr_32, -32768, 32767).astype(np.int16)

    # 2. Bitcrush
    if current_bitcrush > 0:
        arr = (arr >> current_bitcrush) << current_bitcrush

    # 3. Downsample
    if current_downsample > 1:
        sub = arr[::current_downsample]
        arr = np.repeat(sub, current_downsample)[:len(arr)]

    # 4. Stutter
    if current_stutter_ms > 0 and current_stutter_count > 0:
        stutter_samples = int(32000 * (current_stutter_ms / 1000.0))
        start_idx = min(stutter_samples, len(arr) // 4)
        if len(arr) > start_idx + stutter_samples * (current_stutter_count + 1):
            stutter_chunk = arr[start_idx : start_idx + stutter_samples].copy()
            for i in range(current_stutter_count):
                insert_idx = start_idx + stutter_samples * (i + 1)
                arr[insert_idx : insert_idx + stutter_samples] = stutter_chunk

    return arr.tobytes()


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
FILLER_DIR = ROOT / "static" / "fillers"
WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL", "tiny.en")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "en")
FILLER_THRESHOLD_SECS = float(os.getenv("FILLER_THRESHOLD_SECS", "2.0"))
_whisper_model: WhisperModel | None = None
_whisper_lock = asyncio.Lock()
_filler_engine: FillerEngine | None = None

@asynccontextmanager
async def lifespan(app_: FastAPI):
    global _filler_engine
    _filler_engine = FillerEngine(FILLER_DIR)

    if os.getenv("VOICE_SATELLITE_SKIP_GENIE_INIT") == "1":
        logger.info("Skipping Genie TTS startup initialization")
    else:
        app_.state.tts_client = create_tts_client()
        try:
            await app_.state.tts_client.initialize()
        except Exception as error:
            logger.error("Genie TTS startup initialization failed: %s", error)

    try:
        yield
    finally:
        tts_client = getattr(app_.state, "tts_client", None)
        if tts_client is not None:
            await tts_client.stop()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


async def get_whisper_model() -> WhisperModel:
    global _whisper_model

    async with _whisper_lock:
        if _whisper_model is None:
            logger.info("Loading faster-whisper model '%s' on CPU", WHISPER_MODEL_NAME)
            _whisper_model = await asyncio.to_thread(
                WhisperModel,
                WHISPER_MODEL_NAME,
                device="cpu",
                compute_type="int8",
            )
            logger.info("Loaded faster-whisper model '%s'", WHISPER_MODEL_NAME)
    return _whisper_model


async def transcribe_audio(audio_data: bytes) -> str:
    logger.info("Received %s bytes of PCM audio for STT", len(audio_data))
    if len(audio_data) < 2:
        return ""

    audio = np.frombuffer(audio_data, dtype="<i2").astype(np.float32) / 32768.0
    model = await get_whisper_model()

    def run_whisper() -> str:
        segments, _info = model.transcribe(
            audio,
            beam_size=1,
            language=WHISPER_LANGUAGE,
            vad_filter=False,
            condition_on_previous_text=False,
            initial_prompt="A user asks a question or gives a command.",
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

    return await asyncio.to_thread(run_whisper)


def create_router() -> LLMRouter:
    return LLMRouter()


def create_gateway() -> OpenClawGatewayClient:
    return OpenClawGatewayClient()


def create_tts_client() -> GenieTTSClient:
    return GenieTTSClient()


def get_tts_client() -> GenieTTSClient:
    if not hasattr(app.state, "tts_client"):
        app.state.tts_client = create_tts_client()
    return app.state.tts_client


async def stream_openclaw_response(decision: RouterDecision) -> AsyncIterator[str]:
    async with create_gateway() as gateway:
        if decision.mode == "persistent":
            async for chunk in gateway.stream_persistent_send(
                decision.agent_id,
                decision.session_key,
                decision.message,
            ):
                yield chunk
        else:
            async for chunk in gateway.stream_one_shot(decision.agent_id, decision.message):
                yield chunk


async def collect_openclaw_tool_result(websocket: WebSocket, decision: RouterDecision, ws_lock: asyncio.Lock) -> str:
    chunks: list[str] = []
    async for chunk in stream_openclaw_response(decision):
        if not chunk:
            continue
        chunks.append(chunk)
        async with ws_lock:
            await websocket.send_json({"type": "tool_delta", "text": chunk})
    return "".join(chunks)


async def synthesize_sentence(
    websocket: WebSocket,
    sentence: str,
    ws_lock: asyncio.Lock,
    word_offset: int = 0,
) -> None:
    try:
        is_glitch = sentence.startswith("<glitch>")
        clean_sentence = sentence.replace("<glitch>", "").replace("</glitch>", "").strip()

        # GPT-SoVITS hallucinates the reference text if the input is too short,
        # heavily punctuated, or lacks a trailing stop token.
        # 1. Clean up em-dashes and weird characters that confuse the G2P
        clean_sentence = clean_sentence.replace("\u2014", "").replace("-", "").replace("*", "").strip()

        # GPT-SoVITS spells out ALL CAPS words (e.g., P-U-R-G-E) instead of saying them.
        if is_glitch:
            clean_sentence = clean_sentence.lower()

        # 2. Check if there's actually anything to say
        if not any(c.isalnum() for c in clean_sentence):
            return

        # 3. Ensure it ends with punctuation to force a stop token
        if clean_sentence[-1] not in ".!?":
            clean_sentence += "."

        async with ws_lock:
            await websocket.send_json({"type": "status", "state": "tts", "sentence": clean_sentence})

        async for chunk in get_tts_client().synthesize_pcm_chunks(clean_sentence):
            if is_glitch:
                chunk = apply_ordis_glitch(chunk)

            async with ws_lock:
                await websocket.send_json(
                    {
                        "type": "audio",
                        "format": "pcm_s16le_mono",
                        "sample_rate": GENIE_SAMPLE_RATE,
                        "data": encode_pcm_chunk(chunk),
                        "word_offset": word_offset,
                    }
                )
    except asyncio.CancelledError:
        logger.info("TTS synthesis cancelled for sentence %r", sentence[:30])
        raise
    except Exception as error:
        logger.error("Genie TTS failed for sentence %r: %s", sentence[:80], error)


async def speak_text_to_tts(websocket: WebSocket, text: str, ws_lock: asyncio.Lock) -> str:
    sentence_queue: asyncio.Queue[tuple[str, int] | None] = asyncio.Queue()
    pending_sentence_parts: list[str] = []

    async def tts_worker() -> None:
        while True:
            item = await sentence_queue.get()
            try:
                if item is None:
                    return
                sentence, offset = item
                await synthesize_sentence(websocket, sentence, ws_lock, word_offset=offset)
            finally:
                sentence_queue.task_done()

    worker = asyncio.create_task(tts_worker())
    try:
        async with ws_lock:
            await websocket.send_json({"type": "assistant_delta", "text": text})
        sentences, pending_sentence_parts = iter_complete_sentences(
            pending_sentence_parts,
            text,
        )
        word_offset = 0
        for sentence in sentences:
            await sentence_queue.put((sentence, word_offset))
            word_offset += len(sentence.split())

        remainder = "".join(pending_sentence_parts).strip()
        if remainder:
            await sentence_queue.put((remainder, word_offset))

        await sentence_queue.put(None)
        await worker
    finally:
        if not worker.done():
            worker.cancel()

    return text



async def answer_with_qwen_session(
    websocket: WebSocket,
    router: LLMRouter,
    transcript: str,
    decision: CoordinatorDecision,
    ws_lock: asyncio.Lock,
    session: ConversationSession,
    task_tracker: TaskTracker,
) -> str:
    """
    Execute the coordinator decision and speak the result.
    Handles: direct answer, openclaw (blocking tool call), check_tasks, conversation_end.
    Word offsets are threaded through the TTS pipeline for barge-in context.
    """
    assistant_text: str

    if decision.action == "check_tasks":
        # Fast path: speak the status message directly
        assistant_text = decision.message
        await speak_text_to_tts(websocket, assistant_text, ws_lock)

    elif decision.action == "conversation_end":
        # Sign-off: speak farewell, then caller transitions state to passive
        assistant_text = decision.message
        await speak_text_to_tts(websocket, assistant_text, ws_lock)

    elif decision.openclaw is None:
        # Direct answer from LLM
        assistant_text = decision.message
        session.set_current_response(assistant_text)
        await speak_text_to_tts(websocket, assistant_text, ws_lock)

    else:
        # OpenClaw blocking tool call
        tool_id = f"openclaw-{decision.openclaw.agent_id}-{decision.openclaw.mode}"
        async with ws_lock:
            await websocket.send_json(
                {
                    "type": "tool_call_status",
                    "tool_id": tool_id,
                    "tool_name": "openclaw",
                    "name": f"OpenClaw {decision.openclaw.agent_id}",
                    "status": "running",
                    "content": decision.openclaw.message,
                }
            )

        # Race: filler vs tool result
        filler_task: asyncio.Task | None = None
        if _filler_engine and _filler_engine.loaded():
            async def _play_filler_after_delay() -> None:
                await asyncio.sleep(FILLER_THRESHOLD_SECS)
                filler_pcm = _filler_engine.get_filler("working")
                if filler_pcm:
                    async with ws_lock:
                        await websocket.send_json({
                            "type": "audio",
                            "format": "pcm_s16le_mono",
                            "sample_rate": GENIE_SAMPLE_RATE,
                            "data": encode_pcm_chunk(filler_pcm),
                            "word_offset": 0,
                        })
            filler_task = asyncio.create_task(_play_filler_after_delay())

        try:
            tool_result = await collect_openclaw_tool_result(websocket, decision.openclaw, ws_lock)
        finally:
            if filler_task and not filler_task.done():
                filler_task.cancel()

        async with ws_lock:
            await websocket.send_json(
                {
                    "type": "tool_call_status",
                    "tool_id": tool_id,
                    "tool_name": "openclaw",
                    "name": f"OpenClaw {decision.openclaw.agent_id}",
                    "status": "completed",
                    "content": tool_result,
                }
            )
            await websocket.send_json({"type": "status", "state": "assistant_finalizing"})

        # Stream LLM completion to TTS with word offsets
        full_text_chunks: list[str] = []
        sentence_queue: asyncio.Queue[tuple[str, int] | None] = asyncio.Queue()
        pending_sentence_parts: list[str] = []

        async def tts_worker() -> None:
            while True:
                item = await sentence_queue.get()
                try:
                    if item is None:
                        return
                    sentence, offset = item
                    await synthesize_sentence(websocket, sentence, ws_lock, word_offset=offset)
                finally:
                    sentence_queue.task_done()

        worker = asyncio.create_task(tts_worker())

        try:
            word_offset = 0
            async for text_chunk in router.complete_with_tool_result_stream(transcript, decision, tool_result):
                full_text_chunks.append(text_chunk)
                async with ws_lock:
                    await websocket.send_json({"type": "assistant_delta", "text": text_chunk})

                sentences, pending_sentence_parts = iter_complete_sentences(
                    pending_sentence_parts,
                    text_chunk,
                )
                for sentence in sentences:
                    await sentence_queue.put((sentence, word_offset))
                    word_offset += len(sentence.split())

            remainder = "".join(pending_sentence_parts).strip()
            if remainder:
                await sentence_queue.put((remainder, word_offset))

            await sentence_queue.put(None)
            await worker
        finally:
            if not worker.done():
                worker.cancel()

        assistant_text = "".join(full_text_chunks)
        session.set_current_response(assistant_text)

    router.remember_turn(transcript, assistant_text)
    return assistant_text


async def answer_with_openclaw_direct(
    websocket: WebSocket,
    transcript: str,
    ws_lock: asyncio.Lock,
    session: ConversationSession,
) -> str:
    """
    Directly query OpenClaw ('ordis' agent) and stream the response to the user.
    """
    # Start a 1.5s filler task to cover TTFB (Time to First Byte) latency
    filler_task: asyncio.Task | None = None
    if _filler_engine and _filler_engine.loaded():
        async def _play_filler_after_delay() -> None:
            await asyncio.sleep(1.5)
            filler_pcm = _filler_engine.get_filler("thinking")
            if filler_pcm:
                async with ws_lock:
                    await websocket.send_json({
                        "type": "audio",
                        "format": "pcm_s16le_mono",
                        "sample_rate": GENIE_SAMPLE_RATE,
                        "data": encode_pcm_chunk(filler_pcm),
                        "word_offset": 0,
                    })
        filler_task = asyncio.create_task(_play_filler_after_delay())

    full_text_chunks: list[str] = []
    sentence_queue: asyncio.Queue[tuple[str, int] | None] = asyncio.Queue()
    pending_sentence_parts: list[str] = []

    async def tts_worker() -> None:
        while True:
            item = await sentence_queue.get()
            try:
                if item is None:
                    return
                sentence, offset = item
                await synthesize_sentence(websocket, sentence, ws_lock, word_offset=offset)
            finally:
                sentence_queue.task_done()

    worker = asyncio.create_task(tts_worker())

    try:
        word_offset = 0
        async with create_gateway() as gateway:
            async for text_chunk in gateway.stream_persistent_send(
                agent_id="ordis",
                session_key="voice",
                message=transcript,
            ):
                if filler_task and not filler_task.done():
                    filler_task.cancel()  # First byte received, cancel filler

                full_text_chunks.append(text_chunk)
                async with ws_lock:
                    await websocket.send_json({"type": "assistant_delta", "text": text_chunk})

                sentences, pending_sentence_parts = iter_complete_sentences(
                    pending_sentence_parts,
                    text_chunk,
                )
                for sentence in sentences:
                    await sentence_queue.put((sentence, word_offset))
                    word_offset += len(sentence.split())

        remainder = "".join(pending_sentence_parts).strip()
        if remainder:
            await sentence_queue.put((remainder, word_offset))

        await sentence_queue.put(None)
        await worker
    finally:
        if filler_task and not filler_task.done():
            filler_task.cancel()
        if not worker.done():
            worker.cancel()

    assistant_text = "".join(full_text_chunks)
    session.set_current_response(assistant_text)
    return assistant_text


async def send_filler(websocket: WebSocket, ws_lock: asyncio.Lock, category: str) -> None:
    """Send a pre-rendered filler PCM immediately to the frontend."""
    if not _filler_engine or not _filler_engine.loaded():
        return
    pcm = _filler_engine.get_filler(category)
    if not pcm:
        return
    async with ws_lock:
        await websocket.send_json({
            "type": "audio",
            "format": "pcm_s16le_mono",
            "sample_rate": GENIE_SAMPLE_RATE,
            "data": encode_pcm_chunk(pcm),
            "word_offset": 0,
        })


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    logger.info("WebSocket connected")
    router = create_router()
    ws_lock = asyncio.Lock()
    session = ConversationSession()
    task_tracker = TaskTracker()

    async def on_silence_expire() -> None:
        """Called by the silence timer when inactivity timeout fires."""
        if session.is_active():
            session.set_state(SessionState.PASSIVE_LISTENING)
            session.cancel_silence_timer()
            async with ws_lock:
                await websocket.send_json({"type": "status", "state": "passive_listening"})
            logger.info("Silence timeout: returned to passive listening")

    session.set_silence_callback(on_silence_expire)

    async def on_task_complete(task: TrackedTask) -> None:
        """
        Fired when a background OpenClaw task finishes.
        - If the session is active: notify the user immediately.
        - If the session is passive: proactively wake it and notify.
        """
        if task.status == "failed":
            logger.warning("Background task %s failed: %s", task.task_id, task.error)
            return

        summary = task.result or ""
        notify_text = f"Operator, that task is complete. {summary[:200]}" if summary else "Operator, the background task has finished."

        try:
            async with ws_lock:
                await websocket.send_json({
                    "type": "task_complete",
                    "task_id": task.task_id,
                    "summary": summary[:500],
                })

            if session.is_passive():
                # Proactive wake: play ack filler then speak notification
                session.set_state(SessionState.ACKNOWLEDGING)
                await send_filler(websocket, ws_lock, "acknowledge")
                session.set_state(SessionState.ACTIVE_LISTENING)

            await speak_text_to_tts(websocket, notify_text, ws_lock)
            router.remember_turn("[background task completed]", notify_text)

            if session.is_passive():
                session.reset_silence_timer()
        except Exception as exc:
            logger.error("on_task_complete error: %s", exc)

    async def handle_audio(audio_data: bytes) -> None:
        try:
            session.set_state(SessionState.TRANSCRIBING)
            async with ws_lock:
                await websocket.send_json(
                    {
                        "type": "status",
                        "state": "transcribing",
                        "audio_bytes": len(audio_data),
                    }
                )

            transcript = await transcribe_audio(audio_data)
            if not transcript:
                session.set_state(SessionState.ACTIVE_LISTENING)
                async with ws_lock:
                    await websocket.send_json({"type": "status", "state": "active_listening"})
                return

            session.last_transcript = transcript
            session.reset_silence_timer()

            async with ws_lock:
                await websocket.send_json({"type": "transcript", "text": transcript})
                await websocket.send_json({"type": "status", "state": "thinking"})

            session.set_state(SessionState.THINKING)

            # Bypass router and go straight to OpenClaw
            async with ws_lock:
                await websocket.send_json({
                    "type": "decision",
                    "action": "openclaw",
                    "agent_id": "ordis",
                    "mode": "persistent",
                    "reason": "direct connection to openclaw",
                })
                await websocket.send_json({"type": "status", "state": "assistant_streaming"})

            session.set_state(SessionState.SPEAKING)
            assistant_text = await answer_with_openclaw_direct(
                websocket, transcript, ws_lock, session
            )

            async with ws_lock:
                await websocket.send_json({"type": "audio_end"})
                await websocket.send_json({"type": "status", "state": "assistant_complete"})
                await websocket.send_json({"type": "assistant_response", "text": assistant_text})

            session.set_state(SessionState.ACTIVE_LISTENING)
            session.reset_silence_timer()
            async with ws_lock:
                await websocket.send_json({"type": "status", "state": "active_listening"})

        except asyncio.CancelledError:
            logger.info("Background generation task was interrupted.")
            raise
        except Exception as error:
            logger.error("Voice conversation flow failed: %s", error)
            session.set_state(SessionState.ACTIVE_LISTENING)
            async with ws_lock:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": "Voice pipeline failed; see server log.",
                    }
                )
                await websocket.send_json({"type": "status", "state": "active_listening"})

    try:
        while True:
            message = await websocket.receive()

            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))

            audio_data = message.get("bytes")
            if audio_data is None:
                text_data = message.get("text")
                if text_data:
                    try:
                        payload = json.loads(text_data)
                    except json.JSONDecodeError:
                        payload = {}

                    msg_type = payload.get("type", "")

                    if msg_type == "wakeword":
                        # Frontend detected the wakeword — wake up from passive mode
                        if session.is_passive():
                            logger.info("Wakeword received — transitioning to active")
                            session.set_state(SessionState.ACKNOWLEDGING)
                            await send_filler(websocket, ws_lock, "acknowledge")
                            session.set_state(SessionState.ACTIVE_LISTENING)
                            session.reset_silence_timer()
                            async with ws_lock:
                                await websocket.send_json({"type": "status", "state": "active_listening"})

                    elif msg_type == "interrupt":
                        # Barge-in: cancel generation and flush audio
                        partial = await session.cancel_generation()
                        if partial and session.last_transcript:
                            router.remember_turn(
                                session.last_transcript,
                                f"{partial}... [interrupted by user]",
                            )
                        await get_tts_client().stop()
                        session.set_state(SessionState.ACTIVE_LISTENING)
                        session.reset_silence_timer()
                        async with ws_lock:
                            await websocket.send_json({"type": "flush_audio"})
                            await websocket.send_json({"type": "status", "state": "interrupted"})
                            await websocket.send_json({"type": "status", "state": "active_listening"})

                    elif msg_type == "playback_progress":
                        # Frontend reporting which words have been played
                        words_played = int(payload.get("words_played", 0))
                        session.update_words_played(words_played)

                    elif msg_type == "set_timeout":
                        # UI slider updated the silence timeout
                        seconds = float(payload.get("seconds", 30.0))
                        seconds = max(10.0, min(120.0, seconds))
                        session.silence_timeout = seconds
                        logger.info("Silence timeout updated to %.0fs", seconds)

                logger.debug("Ignoring non-binary WebSocket message")
                continue

            # Binary audio: only process in active mode
            if session.is_passive():
                logger.debug("Passive mode: discarding audio (no wakeword)")
                continue

            # Cancel any ongoing generation before starting a new one
            if session.generation_task and not session.generation_task.done():
                partial = await session.cancel_generation()
                if partial and session.last_transcript:
                    router.remember_turn(
                        session.last_transcript,
                        f"{partial}... [interrupted by user]",
                    )
                await get_tts_client().stop()

            generation_task = asyncio.create_task(handle_audio(audio_data))
            session.set_generation_task(generation_task)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as error:
        logger.error("WebSocket endpoint error: %s", error)
    finally:
        await session.close()
        task_tracker.cancel_all()
        try:
            await get_tts_client().stop()
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
