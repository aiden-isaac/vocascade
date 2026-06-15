"""
vocascade/adapter.py — Framework-free FastAPI transport for the vocascade voice loop.

Wires the custom asyncio ``VoicePipeline`` (VAD → STT → waterfall router → TTS)
to a single WebSocket session. This is the T213 "wire the loop" integration:
the running server drives real utterances end-to-end through the confidence
waterfall and speaks character replies, replacing the retired Pipecat
orchestration (kept for reference in ``adapter_legacy.py``).

US3 adds the Hermes backend: an app-level run client + task broker +
delivery coordinator (created once in ``lifespan``, outliving voice sessions).
The hermes skill streams a run's ``message.delta`` output into the current
turn's TTS; results that arrive after the conversation moves on are spoken by
the DeliveryCoordinator at the next idle moment (injected through ``inject``).

Wire protocol (matches ``static/index.html`` and ``RawFrameSerializer``):
  • client → server: one binary WS message per complete utterance (client-side
    VAD endpoints it); JSON control messages ``{"type": "wakeword"|"interrupt"|…}``.
  • server → client: JSON ``{"type": "audio", "data": <base64 pcm>, …}`` plus
    JSON status/transcript/assistant_response messages.
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
from vocascade.session.state_machine import SessionMachine
from vocascade.transport.serializer import RawFrameSerializer
from vocascade.transport.server import transport_auth_from_config
from vocascade.hermes_run_client import HermesRunClient
from vocascade.delivery import DeliveryCoordinator
from vocascade.task_broker import TaskBroker
from vocascade.filler_engine import FillerEngine
from vocascade.pipeline.latency import LatencyMasker, FillerProvider
from vocascade.pipeline.pipeline import (
    VoicePipeline,
    PipelineStage,
    Frame,
    AudioFrame,
    TextFrame,
    ControlMessageFrame,
    InterruptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
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
    never call ``websocket.send`` concurrently. It also drives the delivery gate
    (notify_* hooks) from the frames it observes, since every forwarded frame
    passes through here.
    """

    def __init__(self, outbound: "asyncio.Queue[str]", serializer: RawFrameSerializer,
                 delivery: DeliveryCoordinator | None = None,
                 machine: SessionMachine | None = None):
        super().__init__()
        self.outbound = outbound
        self.serializer = serializer
        self.delivery = delivery
        self.machine = machine   # session lifecycle + teardown (US5)

    async def push(self, frame: Frame):
        if isinstance(frame, AudioFrame):
            data = self.serializer.serialize(frame)
            if data is not None:
                await self.outbound.put(data)
        elif isinstance(frame, ControlMessageFrame):
            if frame.message.get("type") == "transcript" and self.delivery is not None:
                # A transcription reached routing — keep the channel busy so a
                # proactive result doesn't talk over the impending reply.
                self.delivery.notify_response_pending()
            data = self.serializer.serialize(frame)
            if data is not None:
                await self.outbound.put(data)
        elif isinstance(frame, UserStartedSpeakingFrame):
            if self.machine is not None:
                self.machine.on_user_engaged()   # re-engage disarms a pending teardown
            if self.delivery is not None:
                self.delivery.notify_user_started_speaking()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            if self.delivery is not None:
                self.delivery.notify_user_stopped_speaking()
        elif isinstance(frame, BotStartedSpeakingFrame):
            if self.machine is not None:
                self.machine.on_bot_started()
            if self.delivery is not None:
                self.delivery.notify_bot_started_speaking()
            await self.outbound.put(json.dumps({"type": "status", "state": "assistant_streaming"}))
        elif isinstance(frame, BotStoppedSpeakingFrame):
            await self.outbound.put(json.dumps({"type": "audio_end"}))
            if self.machine is not None and self.machine.should_teardown:
                # Farewell / ENDSESSION: return to passive. In-flight tasks are
                # retained (the broker is not touched); queued results are held
                # for the next session (FR-061/FR-062).
                self.machine.on_teardown()
                if self.delivery is not None:
                    self.delivery.unbind_session()
                await self.outbound.put(json.dumps({"type": "status", "state": "passive_listening"}))
            else:
                if self.machine is not None:
                    self.machine.on_bot_stopped()
                if self.delivery is not None:
                    self.delivery.notify_bot_stopped_speaking()
                await self.outbound.put(json.dumps({"type": "status", "state": "active_listening"}))
        # Terminal sink: nothing downstream.


@asynccontextmanager
async def lifespan(app_: FastAPI):
    config = load_config()
    app_.state.config = config

    # Transport auth gate (US8 / OQ-3). Built here so an invalid/missing auth
    # mode fails fast at startup — the endpoint can never default to open (FR-111).
    app_.state.transport_auth = transport_auth_from_config(config)

    logger.info("Loading Whisper STT model '%s' (%s)...", config.whisper_model, config.whisper_language)
    app_.state.stt = WhisperSTT(model_name=config.whisper_model, language=config.whisper_language)

    # Register bundled + user skills, then apply config (drop disabled, US6).
    registry.discover_bundled_skills()
    registry.discover_user_skills()              # user_skills/ — import-isolated (FR-022)
    registry.configure(config.skills_config)     # disabled-skill exclusion + per-skill config
    logger.info("Registered skills: %s", [s.name for s in registry.get_all_skills()])

    app_.state.degraded_tts = not (
        config.tts_onnx_model_dir and config.tts_reference_audio and config.tts_reference_text
    )
    if app_.state.degraded_tts:
        logger.warning(
            "Genie TTS in degraded mode (text replies only) — voice-cloning env "
            "(GENIE_ONNX_MODEL_DIR / GENIE_REFERENCE_AUDIO / GENIE_REFERENCE_TEXT) not fully set."
        )

    # Hermes backend (US3): one run client + broker + delivery coordinator for
    # the app's lifetime — tasks outlive voice sessions (FR-061). No startup
    # network probe; the broker probes capabilities lazily on first dispatch.
    run_client = HermesRunClient(
        base_url=config.hermes_base_url,
        api_key=config.hermes_api_key,
        session_key=config.hermes_session_key,
        model=config.hermes_model,
    )
    app_.state.run_client = run_client
    app_.state.delivery = DeliveryCoordinator(speech_budget=config.result_speech_budget)
    app_.state.task_broker = TaskBroker(run_client, app_.state.delivery)
    logger.info("Hermes broker ready (backend %s)", config.hermes_base_url)

    # Latency masking (US4): dynamic spoken fillers + progressive follow-ups.
    # Pre-rendered clips are used ONLY for the wakeword acknowledge.
    filler_engine = FillerEngine(config.filler_dir)
    app_.state.latency = LatencyMasker(
        filler_engine,
        FillerProvider(mode=config.filler_mode),
        interval=config.filler_interval_seconds,
        backoff=config.filler_backoff,
        max_fillers=config.filler_max,
    )
    logger.info("Latency masker ready (mode=%s, interval=%.1fs, max=%d; ack clips=%d)",
                config.filler_mode, config.filler_interval_seconds, config.filler_max,
                filler_engine.get_categories().get("acknowledge", 0))

    yield

    logger.info("Shutting down vocascade adapter...")
    await app_.state.task_broker.shutdown()
    try:
        await run_client.aclose()
    except Exception:  # best-effort; client may never have opened a connection
        pass
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
                    outbound: "asyncio.Queue[str]", serializer: RawFrameSerializer,
                    task_broker: TaskBroker | None = None,
                    delivery: DeliveryCoordinator | None = None,
                    latency: LatencyMasker | None = None):
    """Assemble a fresh per-session VoicePipeline: VAD → STT → router → TTS → transport-out."""
    session_state = SessionState(voice_session_id=str(uuid.uuid4()), state=SessionStateEnum.ACTIVE)
    machine = SessionMachine(session_state)
    router = WaterfallRouter.from_config(config)

    stages = [
        VADStage(server_vad_enabled=config.server_vad_enabled, sample_rate=config.audio_in_sample_rate),
        STTStage(whisper_stt=stt),
        RouterStage(router=router, session_state=session_state, config=config,
                    task_broker=task_broker, latency=latency, delivery=delivery),
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
        TransportOutputStage(outbound, serializer, delivery=delivery, machine=machine),
    ]
    pipeline = VoicePipeline(stages)
    # The TTS sink needs explicit teardown beyond pipeline.stop() (closes aiohttp)
    # and is the injection point for proactive (system-initiated) speech.
    pipeline.tts_stage = stages[-2]
    pipeline.session_machine = machine
    return pipeline


async def _handle_control(text: str, pipeline: VoicePipeline, outbound: "asyncio.Queue[str]",
                          delivery: DeliveryCoordinator, inject, inject_audio,
                          masker: LatencyMasker, broker: TaskBroker | None = None) -> None:
    """Handle a JSON control message from the client."""
    try:
        msg = json.loads(text)
    except (ValueError, TypeError):
        logger.warning("Ignoring non-JSON control message")
        return

    mtype = msg.get("type")
    if mtype == "wakeword":
        # Session is live; a fresh turn may follow. Clear any prior interrupt arm
        # and let proactive deliveries flow (idle-gated) for this session.
        pipeline.interrupt_event.clear()
        machine = getattr(pipeline, "session_machine", None)
        if machine is not None:
            machine.on_wake()   # passive → active (disarms any pending teardown)
        delivery.bind_session(inject)
        await outbound.put(json.dumps({"type": "status", "state": "active_listening"}))
        # Instant pre-rendered acknowledgement ("Yes?") — the only pre-rendered
        # audio in use; everything else is dynamically generated (US4 / FR-046).
        pcm = masker.filler_engine.get_filler("acknowledge") if masker.filler_engine else None
        if pcm:
            await inject_audio(pcm)
    elif mtype == "interrupt":
        # Client-side barge-in: kill any in-flight proactive delivery and abort
        # in-flight synthesis. The next utterance re-arms the loop.
        delivery.notify_interruption()
        pipeline.interrupt_event.set()
        await pipeline.push(InterruptionFrame())
        # A barge-in abandons the current activity: cancel this session's in-flight
        # Hermes runs so an interrupted query's result is not pushed proactively
        # later (e.g. "scratch that, do X instead"). Going silent is NOT a barge-in,
        # so the legitimate late-delivery path (FR-052) is preserved.
        if broker is not None:
            machine = getattr(pipeline, "session_machine", None)
            sid = machine.state.voice_session_id if machine is not None else ""
            for task in list(broker.active_tasks()):
                if not sid or task.session_id == sid:
                    await broker.cancel(task.task_id)
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

        # Transport auth gate (US8 / OQ-3): trust-network passes through; in
        # device-identity mode the client must complete the Ed25519 handshake
        # before any audio flows. A reject closes with 1008 (policy violation).
        auth_result = await app.state.transport_auth.authenticate(websocket)
        if not auth_result.ok:
            logger.warning("Rejecting connection: transport auth failed (%s)", auth_result.reason)
            await websocket.close(code=1008)
            return
        logger.info("WebSocket session opened (%s)", auth_result.reason)

        config = app.state.config
        delivery: DeliveryCoordinator = app.state.delivery
        broker: TaskBroker = app.state.task_broker
        serializer = RawFrameSerializer()
        outbound: "asyncio.Queue[str]" = asyncio.Queue()
        pipeline = _build_pipeline(
            config, app.state.stt, app.state.degraded_tts, outbound, serializer,
            task_broker=broker, delivery=delivery, latency=app.state.latency,
        )
        await pipeline.start()

        # Proactive (system-initiated) speech is injected as a TextFrame straight
        # into TTS; its BotStarted/Stopped frames drive the delivery gate.
        async def inject(text: str) -> None:
            await pipeline.tts_stage.push(TextFrame(text=text))

        # Pre-rendered ack clips are at the output sample rate; play them straight
        # through TTS (it forwards AudioFrames untouched to the transport).
        async def inject_audio(pcm: bytes) -> None:
            await pipeline.tts_stage.push(AudioFrame(audio=pcm, sample_rate=config.audio_out_sample_rate))

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
                    await _handle_control(message["text"], pipeline, outbound, delivery,
                                          inject, inject_audio, app.state.latency, broker)
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected by client")
        except Exception as exc:
            logger.error("WebSocket session error: %s", exc, exc_info=True)
        finally:
            # Session ends: unbind delivery but DO NOT shut down the broker —
            # in-flight Hermes tasks are retained and delivered next session (FR-061).
            delivery.unbind_session()
            await pipeline.stop()
            await pipeline.tts_stage.close()
            sender_task.cancel()
            try:
                await sender_task
            except asyncio.CancelledError:
                pass
            logger.info("WebSocket session closed")
