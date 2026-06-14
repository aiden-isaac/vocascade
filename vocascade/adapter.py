"""
vocascade/adapter.py — Framework-free FastAPI transport for the vocascade voice loop.

Wires the custom asyncio ``VoicePipeline`` (VAD → STT → waterfall router → TTS)
to a single WebSocket session. This is the T213 "wire the loop" integration:
the running server now drives real utterances end-to-end through the confidence
waterfall and speaks character replies, replacing the retired Pipecat
orchestration (kept for reference in ``adapter_legacy.py``).

Wire protocol (matches ``static/index.html`` and ``RawFrameSerializer``):
  • client → server: one binary WS message per complete utterance (client-side
    VAD endpoints it); JSON control messages ``{"type": "wakeword"|"interrupt"|…}``.
  • server → client: JSON ``{"type": "audio", "data": <base64 pcm>, …}`` plus
    JSON status/transcript/assistant_response messages.

Hermes is a passthrough mockup at this phase (US3/T223 wires the real backend).
"""

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect, WebSocketState

from vocascade.config import load_config
from vocascade.stt.whisper import WhisperSTT
from vocascade.waterfall.router import WaterfallRouter
from vocascade.skills.registry import registry
from vocascade.session.state import SessionState, SessionStateEnum
from vocascade.transport.serializer import RawFrameSerializer
from vocascade.pipeline.pipeline import (
    VoicePipeline,
    PipelineStage,
    Frame,
    AudioFrame,
    ControlMessageFrame,
    InterruptionFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
)
from vocascade.pipeline.vad import VADStage
from vocascade.pipeline.stt import STTStage
from vocascade.pipeline.router import RouterStage
from vocascade.pipeline.tts import GenieTTSStage

logger = logging.getLogger("vocascade.adapter")

# At most one active WS session at a time (single-user voice device); concurrent
# connects are rejected with close code 1008.
_session_lock = asyncio.Lock()


class TransportOutputStage(PipelineStage):
    """
    Terminal pipeline sink. Serialises outbound frames onto a per-session queue
    drained by a single sender coroutine, so the receive loop and the pipeline
    never call ``websocket.send`` concurrently. Bot start/stop frames are mapped
    to client status messages.
    """

    def __init__(self, outbound: "asyncio.Queue[str]", serializer: RawFrameSerializer):
        super().__init__()
        self.outbound = outbound
        self.serializer = serializer

    async def push(self, frame: Frame):
        if isinstance(frame, AudioFrame):
            data = self.serializer.serialize(frame)
            if data is not None:
                await self.outbound.put(data)
        elif isinstance(frame, ControlMessageFrame):
            data = self.serializer.serialize(frame)
            if data is not None:
                await self.outbound.put(data)
        elif isinstance(frame, BotStartedSpeakingFrame):
            await self.outbound.put(json.dumps({"type": "status", "state": "assistant_streaming"}))
        elif isinstance(frame, BotStoppedSpeakingFrame):
            await self.outbound.put(json.dumps({"type": "audio_end"}))
            await self.outbound.put(json.dumps({"type": "status", "state": "active_listening"}))
        # Terminal sink: nothing downstream.


@asynccontextmanager
async def lifespan(app_: FastAPI):
    config = load_config()
    app_.state.config = config

    logger.info("Loading Whisper STT model '%s' (%s)...", config.whisper_model, config.whisper_language)
    app_.state.stt = WhisperSTT(model_name=config.whisper_model, language=config.whisper_language)

    # Register bundled skills (smalltalk floor + hermes passthrough for the MVP).
    registry.discover_bundled_skills()
    logger.info("Registered skills: %s", [s.name for s in registry.get_all_skills()])

    app_.state.degraded_tts = not (
        config.tts_onnx_model_dir and config.tts_reference_audio and config.tts_reference_text
    )
    if app_.state.degraded_tts:
        logger.warning(
            "Genie TTS in degraded mode (text replies only) — voice-cloning env "
            "(GENIE_ONNX_MODEL_DIR / GENIE_REFERENCE_AUDIO / GENIE_REFERENCE_TEXT) not fully set."
        )

    yield

    logger.info("Shutting down vocascade adapter...")
    app_.state.stt.close()


app = FastAPI(title="Vocascade Voice Server", lifespan=lifespan)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> HTMLResponse:
    """Serve the browser client, or a fallback banner if it is missing."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Vocascade voice server</h1><p>WebSocket endpoint: <code>/ws</code></p>")


def _build_pipeline(config, stt: WhisperSTT, degraded_tts: bool,
                    outbound: "asyncio.Queue[str]", serializer: RawFrameSerializer):
    """Assemble a fresh per-session VoicePipeline: VAD → STT → router → TTS → transport-out."""
    session_state = SessionState(voice_session_id=str(uuid.uuid4()), state=SessionStateEnum.ACTIVE)
    router = WaterfallRouter.from_config(config)

    stages = [
        VADStage(server_vad_enabled=config.server_vad_enabled, sample_rate=config.audio_in_sample_rate),
        STTStage(whisper_stt=stt),
        RouterStage(router=router, session_state=session_state, config=config),
        GenieTTSStage(
            tts_url=config.tts_url,
            character_name=config.tts_character_name,
            onnx_model_dir=config.tts_onnx_model_dir,
            reference_audio=config.tts_reference_audio,
            reference_text=config.tts_reference_text,
            language=config.tts_language,
            degraded_mode=degraded_tts,
            sample_rate=config.audio_out_sample_rate,
        ),
        TransportOutputStage(outbound, serializer),
    ]
    pipeline = VoicePipeline(stages)
    # The TTS sink needs explicit teardown beyond pipeline.stop() (closes aiohttp).
    pipeline.tts_stage = stages[-2]
    return pipeline


async def _handle_control(text: str, pipeline: VoicePipeline, outbound: "asyncio.Queue[str]") -> None:
    """Handle a JSON control message from the client."""
    try:
        msg = json.loads(text)
    except (ValueError, TypeError):
        logger.warning("Ignoring non-JSON control message")
        return

    mtype = msg.get("type")
    if mtype == "wakeword":
        # Session is live; a fresh turn may follow. Clear any prior interrupt arm.
        pipeline.interrupt_event.clear()
        await outbound.put(json.dumps({"type": "status", "state": "active_listening"}))
    elif mtype == "interrupt":
        # Client-side barge-in: abort any in-flight synthesis. The next utterance
        # re-arms the loop (interrupt_event is cleared when fresh audio arrives).
        pipeline.interrupt_event.set()
        await pipeline.push(InterruptionFrame())
    # set_timeout / playback_progress / unknown: ignored for the MVP loop.


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Single-session WebSocket endpoint driving the custom voice pipeline."""
    if _session_lock.locked():
        logger.warning("Rejecting connection: a session is already active")
        await websocket.accept()
        await websocket.send_json({"type": "error", "message": "Session already active. Please wait."})
        await websocket.close(code=1008)
        return

    async with _session_lock:
        await websocket.accept()
        logger.info("WebSocket session opened")

        config = app.state.config
        serializer = RawFrameSerializer()
        outbound: "asyncio.Queue[str]" = asyncio.Queue()
        pipeline = _build_pipeline(config, app.state.stt, app.state.degraded_tts, outbound, serializer)
        await pipeline.start()

        async def sender() -> None:
            try:
                while True:
                    item = await outbound.get()
                    if websocket.client_state != WebSocketState.CONNECTED:
                        break
                    await websocket.send_text(item)
            except (WebSocketDisconnect, RuntimeError):
                pass  # client gone; receive loop handles teardown
            except Exception as exc:
                logger.error("Sender error: %s", exc)

        sender_task = asyncio.create_task(sender())

        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    # One complete utterance per binary message (client-side VAD).
                    pipeline.interrupt_event.clear()
                    await pipeline.push(AudioFrame(
                        audio=message["bytes"],
                        sample_rate=config.audio_in_sample_rate,
                    ))
                elif message.get("text") is not None:
                    await _handle_control(message["text"], pipeline, outbound)
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected by client")
        except Exception as exc:
            logger.error("WebSocket session error: %s", exc, exc_info=True)
        finally:
            await pipeline.stop()
            await pipeline.tts_stage.close()
            sender_task.cancel()
            try:
                await sender_task
            except asyncio.CancelledError:
                pass
            logger.info("WebSocket session closed")
