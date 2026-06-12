"""
Core module for the Pipecat Voice Adapter.
Defines the FastAPI app, the WebSocket endpoint, the RawFrameSerializer,
the SileroVADService shim, and the AdapterProcessor.
"""

import asyncio
import datetime
import logging
import re
import uuid
from contextlib import asynccontextmanager
from typing import List, Optional

from pipecat.services.llm_service import FunctionCallParams

from pathlib import Path
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

import json
import base64

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    LLMContextFrame,
    LLMMessagesAppendFrame,
    OutputAudioRawFrame,
    StartFrame,
    SystemFrame,
    TranscriptionFrame,
    TextFrame,
    CancelFrame,
    InterruptionFrame,
    OutputTransportMessageUrgentFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
    LLMFullResponseEndFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    UserStartedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.whisper.stt import WhisperSTTService, WhisperSTTSettings
from pipecat.transcriptions.language import Language
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams

from voice_adapter.config import AdapterConfig, load_config
from voice_adapter.delivery import DeliveryCoordinator
from voice_adapter.filler_engine import FillerEngine
from voice_adapter.hermes_run_client import HermesRunClient
from voice_adapter.task_broker import TaskBroker
from voice_adapter.transcript_manager import HermesTaskState, TranscriptManager, TranscriptTurn
from voice_adapter.tts_genie import GenieTTSService

logger = logging.getLogger("voice_adapter.adapter")

# Module-level lock for enforcing single active WebSocket session
_session_lock = asyncio.Lock()

# Session-scoped counter for task ID generation
_task_counter = 0


def generate_hermes_task_id() -> str:
    """Generates task ID in format task_YYYYMMDD_HHMMSS_XX."""
    global _task_counter
    _task_counter += 1
    now = datetime.datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    return f"task_{timestamp}_{_task_counter:02d}"


# ── Session termination detection ────────────────────────────────────────────
# Termination uses TWO independent signals so it does not depend on a small,
# fast local model reliably emitting a sentinel:
#   1. The model appends the ENDSESSION sentinel to its farewell reply.
#   2. A deterministic phrase match on the user's transcript (the backstop).
# Either one returning True ends the session after the bot finishes speaking.

_TERMINATE_SENTINEL = "ENDSESSION"

# Deterministic farewell phrases matched against the normalized user transcript.
# Kept to unambiguous "wrap up" phrasings to avoid ending the session by accident.
_FAREWELL_PHRASES = (
    "that will be all",
    "that'll be all",
    "that is all",
    "thats all",
    "that's all",
    "thats everything",
    "that's everything",
    "thats it for now",
    "that's it for now",
    "nothing else",
    "we are done",
    "we're done",
    "were done",
    "i am done",
    "i'm done",
    "im done",
    "goodbye",
    "good bye",
    "see you later",
    "talk to you later",
    "good night",
    "goodnight",
)


def _normalize(text: str) -> str:
    """Lowercase and strip punctuation, collapsing to bare words for matching."""
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _contains_sentinel(text: str) -> bool:
    """True if the (possibly streamed/spaced) ENDSESSION sentinel is present."""
    return "endsession" in re.sub(r"[^a-z]", "", text.lower())


def _strip_sentinel(text: str) -> str:
    """Remove the termination sentinel so it is never stored or spoken."""
    return re.sub(r"(?i)end\s*session", "", text)


def _is_farewell(text: str) -> bool:
    """Deterministic backstop: True if the user clearly signalled wrap-up."""
    norm = _normalize(text)
    if not norm:
        return False
    return any(phrase in norm for phrase in _FAREWELL_PHRASES)


class ClientMessageFrame(SystemFrame):
    def __init__(self, message: dict):
        super().__init__()
        self.message = message

class RawFrameSerializer(FrameSerializer):
    """Serializer converting raw PCM audio bytes to/from Pipecat raw audio frames, and handling JSON for control/status."""

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, OutputAudioRawFrame):
            b64_audio = base64.b64encode(frame.audio).decode("utf-8")
            return json.dumps({
                "type": "audio",
                "data": b64_audio,
                "sample_rate": getattr(frame, "sample_rate", 32000)
            })
        elif isinstance(frame, OutputTransportMessageUrgentFrame):
            return json.dumps(frame.message)
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, bytes):
            return InputAudioRawFrame(
                audio=data,
                sample_rate=16000,
                num_channels=1
            )
        if isinstance(data, str):
            try:
                msg = json.loads(data)
                logger.info(f"Deserialized client message: {msg}")
                return ClientMessageFrame(message=msg)
            except Exception as e:
                logger.error(f"Error parsing client JSON: {e}")
        return None


class EdgeVADProcessor(FrameProcessor):
    """
    Since the Edge Client (web UI) already performs VAD and sends complete speech
    segments as distinct chunks, we bypass Silero and simply inject the required
    Pipecat VAD frames around the audio to trigger the downstream STT service.
    """
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame):
            await self.push_frame(VADUserStartedSpeakingFrame(), direction)
            await self.push_frame(frame, direction)
            await self.push_frame(VADUserStoppedSpeakingFrame(), direction)
            return

        await self.push_frame(frame, direction)


class SileroVADService:
    """Shim class satisfying SileroVADService requirements using EdgeVADProcessor."""
    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.processor = EdgeVADProcessor()


class AdapterProcessor(FrameProcessor):
    """
    Custom FrameProcessor representing the Voice Adapter logic.
    In Phase 3 MVP, it manages the sliding-window conversation history,
    injects a minimal system prompt, and intercepts user transcriptions
    to route them directly to the Qwen LLM via OpenAILLMService.
    """

    def __init__(
        self,
        transcript_manager: TranscriptManager,
        config: AdapterConfig,
        tools: Optional[ToolsSchema] = None,
        filler_engine: Optional[FillerEngine] = None,
        delivery: Optional[DeliveryCoordinator] = None,
    ):
        super().__init__()
        self.transcript_manager = transcript_manager
        self.config = config
        self.tools = tools
        self.filler_engine = filler_engine
        self.delivery = delivery
        # Set once the TeardownInterceptor exists; lets the deterministic
        # farewell backstop arm a session teardown for the current reply.
        self.teardown: Optional["TeardownInterceptor"] = None
        self.system_message = (
            "You are a helpful voice assistant. Keep your responses concise and brief. "
            "Only if the user explicitly says goodbye, farewell, thank you that will be all, "
            "or a clear and direct farewell phrase, append the single word ENDSESSION on a new line "
            "at the very end of your response, with no other text after it. "
            "Do NOT append ENDSESSION for greetings, questions, or any non-farewell message."
        )

    async def _play_filler(self, category: str, direction: FrameDirection) -> None:
        """Push a pre-rendered acknowledgement clip for instant (zero-latency) playback."""
        if not self.filler_engine:
            return
        pcm = self.filler_engine.get_filler(category)
        if not pcm:
            return
        await self.push_frame(
            OutputAudioRawFrame(
                audio=pcm,
                sample_rate=self.config.audio_out_sample_rate,
                num_channels=1,
            ),
            direction,
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        
        logger.info(f"AdapterProcessor received: {type(frame).__name__}")

        if isinstance(frame, ClientMessageFrame):
            msg_type = frame.message.get("type")
            logger.info(f"Processing ClientMessageFrame: {msg_type}")
            if msg_type == "wakeword":
                logger.info("Wakeword received, pushing status active_listening")
                msg = OutputTransportMessageUrgentFrame({"type": "status", "state": "active_listening"})
                await self.push_frame(msg, direction)
                # Instant audible acknowledgement ("Yes?", "I'm listening.")
                await self._play_filler("acknowledge", direction)
                # The session is live: proactive deliveries may flow (and any
                # backlog held from a previous session drains, idle-gated).
                if self.delivery is not None:
                    self.delivery.bind_session(self.inject_text)
                return
            elif msg_type == "interrupt":
                await self.push_frame(InterruptionFrame(), direction)
                return
            return

        elif isinstance(frame, StartFrame):
            # Update TranscriptManager with the system prompt directly
            has_system = any(turn.role == "system" for turn in self.transcript_manager.get_window())
            if not has_system:
                self.transcript_manager.append(TranscriptTurn(
                    role="system",
                    content=self.system_message
                ))

            # Pass StartFrame downstream first
            await self.push_frame(frame, direction)

            # Inject the minimal system prompt frame once when the pipeline starts
            sys_frame = LLMMessagesAppendFrame(
                messages=[{"role": "system", "content": self.system_message}]
            )
            await self.push_frame(sys_frame, direction)
            return

        elif isinstance(frame, LLMMessagesAppendFrame):
            # Capture the system message and append to the TranscriptManager
            for msg in frame.messages:
                self.transcript_manager.append(TranscriptTurn(
                    role=msg.get("role", "system"),
                    content=msg.get("content", ""),
                ))
            await self.push_frame(frame, direction)
            return

        elif isinstance(frame, (VADUserStartedSpeakingFrame, UserStartedSpeakingFrame)):
            if self.delivery is not None:
                self.delivery.notify_user_started_speaking()
            await self.push_frame(frame, direction)
            return

        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            if self.delivery is not None:
                self.delivery.notify_user_stopped_speaking()
            await self.push_frame(frame, direction)
            return

        elif isinstance(frame, TranscriptionFrame):
            # A reply is now genuinely pending; hold proactive deliveries
            # until the bot finishes speaking it.
            if self.delivery is not None:
                self.delivery.notify_response_pending()
            msg = OutputTransportMessageUrgentFrame({"type": "status", "state": "assistant_streaming"})
            await self.push_frame(msg, direction)
            
            transcript_msg = OutputTransportMessageUrgentFrame({"type": "transcript", "text": frame.text})
            await self.push_frame(transcript_msg, direction)

            # 1. Append user transcription to TranscriptManager
            user_turn = TranscriptTurn(
                role="user",
                content=frame.text,
            )
            self.transcript_manager.append(user_turn)

            # Deterministic farewell backstop: arm a session teardown so we return
            # to passive/wakeword mode even if the model forgets the ENDSESSION sentinel.
            if self.teardown is not None and _is_farewell(frame.text):
                logger.info("Deterministic farewell phrase detected — arming session teardown.")
                self.teardown.arm_termination()

            # 2. Reconstruct the full message list from the sliding window
            turns = self.transcript_manager.get_window()
            messages = []
            for turn in turns:
                messages.append({
                    "role": turn.role,
                    "content": turn.content
                })

            # Create the LLMContext (advertising any tools to the model) and
            # wrap it in an LLMContextFrame.
            context = LLMContext(messages=messages)
            if self.tools is not None:
                context.set_tools(self.tools)
            context_frame = LLMContextFrame(context=context)

            # 3. Push LLMContextFrame downstream
            await self.push_frame(context_frame, direction)
            return

        elif isinstance(frame, TextFrame):
            clean = _strip_sentinel(frame.text).strip()
            msg = OutputTransportMessageUrgentFrame({"type": "assistant_response", "text": clean})
            await self.push_frame(msg, direction)
            await self.push_frame(frame, direction)
            return

        # Propagate all other frames
        await self.push_frame(frame, direction)

    async def inject_text(self, text: str):
        """Asynchronously injects text frames downstream (used by background tools)."""
        logger.info(f"Injecting text from background task: {text[:50]}...")
        msg = OutputTransportMessageUrgentFrame({"type": "assistant_response", "text": text})
        await self.push_frame(msg, FrameDirection.DOWNSTREAM)
        await self.push_frame(TextFrame(text), FrameDirection.DOWNSTREAM)


class TeardownInterceptor(FrameProcessor):
    """
    Sits just downstream of the LLM. Buffers the assistant's reply (so it can be
    committed to history), strips the ENDSESSION sentinel before it reaches TTS,
    and — once the bot finishes speaking a farewell — returns the client to
    passive/wakeword listening.

    Termination fires when EITHER signal is present:
      * the model emitted the ENDSESSION sentinel in this reply, or
      * the deterministic farewell backstop armed it (see AdapterProcessor).
    """
    def __init__(
        self,
        transcript_mgr: TranscriptManager,
        delivery: Optional[DeliveryCoordinator] = None,
    ):
        super().__init__()
        self.transcript_mgr = transcript_mgr
        self.delivery = delivery
        self._full_response_buffer = ""
        # Armed by the deterministic farewell phrase match on the user transcript.
        self._termination_armed = False

    def arm_termination(self) -> None:
        """Deterministic backstop: end the session after the current reply is spoken."""
        self._termination_armed = True

    async def _go_passive(self) -> None:
        logger.info("Session termination — returning client to passive (wakeword) listening.")
        # Undelivered proactive results are retained for the next session.
        if self.delivery is not None:
            self.delivery.unbind_session()
        msg = OutputTransportMessageUrgentFrame({"type": "status", "state": "passive_listening"})
        await self.push_frame(msg, FrameDirection.DOWNSTREAM)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TextFrame):
            self._full_response_buffer += frame.text
            # Best-effort strip here; GenieTTSService.run_tts does the authoritative
            # removal after sentence aggregation reassembles split tokens.
            clean_text = _strip_sentinel(frame.text)
            if clean_text.strip():
                await self.push_frame(TextFrame(clean_text), direction)
            return

        if isinstance(frame, BotStartedSpeakingFrame):
            if self.delivery is not None:
                self.delivery.notify_bot_started_speaking()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, (UserStartedSpeakingFrame, InterruptionFrame, VADUserStartedSpeakingFrame)):
            # Barge-in: a delivery in flight is stopped and NOT re-queued; the
            # partial text is committed below with the [interrupted] marker.
            if self.delivery is not None:
                self.delivery.notify_user_started_speaking()
            buffered = self._full_response_buffer
            if buffered.strip():
                self.transcript_mgr.append(TranscriptTurn(
                    role="assistant",
                    content=_strip_sentinel(buffered).strip() + " ... [interrupted]",
                    was_interrupted=True,
                ))
            self._full_response_buffer = ""
            # The user re-engaged mid-reply — cancel any pending teardown so a
            # farewell that gets interrupted keeps the conversation alive.
            self._termination_armed = False
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, BotStoppedSpeakingFrame):
            buffered = self._full_response_buffer
            should_terminate = self._termination_armed or _contains_sentinel(buffered)
            if buffered.strip():
                self.transcript_mgr.append(TranscriptTurn(
                    role="assistant",
                    content=_strip_sentinel(buffered).strip(),
                ))
            self._full_response_buffer = ""
            self._termination_armed = False
            await self.push_frame(frame, direction)
            if should_terminate:
                await self._go_passive()
            elif self.delivery is not None:
                # Channel just went quiet — queued proactive results may flow.
                self.delivery.notify_bot_stopped_speaking()
            return

        await self.push_frame(frame, direction)


def build_pipeline(
    transport: FastAPIWebsocketTransport,
    vad_processor: VADProcessor,
    stt_service: WhisperSTTService,
    adapter_processor: AdapterProcessor,
    llm_service: OpenAILLMService,
    teardown_interceptor: TeardownInterceptor,
    tts_service: GenieTTSService,
) -> Pipeline:
    """Assembles and returns the core Pipecat pipeline."""
    return Pipeline([
        transport.input(),
        vad_processor,
        stt_service,
        adapter_processor,
        llm_service,
        teardown_interceptor,
        tts_service,
        transport.output(),
    ])


@asynccontextmanager
async def lifespan(app_: FastAPI):
    # Load config
    config = load_config()
    app_.state.config = config

    # Load pre-rendered acknowledgement / filler audio into RAM
    app_.state.filler_engine = FillerEngine(config.filler_dir)

    # Initialize Whisper STT Service
    logger.info("Initializing Whisper STT service...")
    app_.state.stt_service = WhisperSTTService(
        device="cpu",
        settings=WhisperSTTSettings(
            model=config.whisper_model,
            language=Language(config.whisper_language)
        )
    )

    # Initialize Genie TTS Service
    degraded = not (config.tts_onnx_model_dir and config.tts_reference_audio and config.tts_reference_text)
    logger.info("Initializing Genie TTS service (degraded=%s)...", degraded)
    app_.state.tts_service = GenieTTSService(
        tts_url=config.tts_url,
        character_name=config.tts_character_name,
        onnx_model_dir=config.tts_onnx_model_dir,
        reference_audio=config.tts_reference_audio,
        reference_text=config.tts_reference_text,
        language=config.tts_language,
        degraded_mode=degraded,
        sample_rate=config.audio_out_sample_rate
    )
    if not config.skip_genie_init and not degraded:
        await app_.state.tts_service.preload()

    # Hermes agent backend (005): one run client + broker + delivery
    # coordinator for the app's lifetime — tasks outlive voice sessions.
    run_client = HermesRunClient(
        base_url=config.hermes_base_url,
        api_key=config.hermes_api_key,
        session_key=config.hermes_session_key,
        model=config.hermes_model,
    )
    delivery = DeliveryCoordinator(speech_budget=config.result_speech_budget)
    app_.state.run_client = run_client
    app_.state.delivery = delivery
    app_.state.task_broker = TaskBroker(run_client, delivery)

    caps = await run_client.probe_capabilities()
    if caps.supports_runs:
        logger.info(
            "Hermes runs API available (model=%s, auth_required=%s)",
            caps.model_name, caps.auth_required,
        )
    elif caps.supports_chat:
        logger.warning("Hermes runs API unavailable — chat-completions fallback mode")
    else:
        logger.warning(
            "Hermes unreachable at %s — dispatches will fail until it returns",
            config.hermes_base_url,
        )

    yield

    # Cleanup resources
    logger.info("Cleaning up services...")
    if hasattr(app_.state, "task_broker"):
        await app_.state.task_broker.shutdown()
    if hasattr(app_.state, "run_client"):
        await app_.state.run_client.aclose()
    if hasattr(app_.state, "tts_service"):
        await app_.state.tts_service.close()


app = FastAPI(lifespan=lifespan)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> HTMLResponse:
    """Serves the index.html page or a fallback message if it doesn't exist."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Voice Satellite Core Client</h1>")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for edge physical client (satellite.py).
    Enforces a single concurrent session policy.
    """
    if _session_lock.locked():
        logger.warning("Rejecting connection: session already active")
        await websocket.accept()
        await websocket.send_json({
            "type": "error",
            "message": "Session already active. Please wait."
        })
        await websocket.close(code=1008)
        return

    async with _session_lock:
        await websocket.accept()
        logger.info("WebSocket connection accepted")

        config = getattr(app.state, "config", None) or load_config()

        # Build transport using custom RawFrameSerializer
        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                audio_in_sample_rate=config.audio_in_sample_rate,
                audio_out_sample_rate=config.audio_out_sample_rate,
                serializer=RawFrameSerializer(),
            )
        )

        stt_service = app.state.stt_service
        tts_service = app.state.tts_service

        vad_service = SileroVADService(sample_rate=config.audio_in_sample_rate)

        # App-level Hermes backend (created in lifespan; tasks outlive sessions)
        task_broker: TaskBroker = app.state.task_broker
        delivery: DeliveryCoordinator = app.state.delivery
        # Per-voice-session conversational continuity id (X-Hermes-Session-Id)
        voice_session_id = str(uuid.uuid4())

        llm = OpenAILLMService(
            api_key=config.llm_api_key or "not-needed",
            base_url=config.llm_base_url,
            settings=OpenAILLMService.Settings(
                model=config.llm_model,
            ),
        )

        filler_engine = getattr(app.state, "filler_engine", None)

        # Define the Hermes tool schema (advertised to the model via the
        # LLMContext; pipecat 1.3 requires ToolsSchema, not raw dicts)
        hermes_tool_schema = FunctionSchema(
            name="query_hermes_agent",
            description=(
                "Invoke the Hermes agent to perform complex tasks, run "
                "integrations, or fetch external information that you cannot "
                "handle locally."
            ),
            properties={
                "prompt": {
                    "type": "string",
                    "description": "The exact user request or context to send to Hermes.",
                }
            },
            required=["prompt"],
        )
        tools_schema = ToolsSchema(standard_tools=[hermes_tool_schema])

        transcript_manager = TranscriptManager()
        adapter_processor = AdapterProcessor(
            transcript_manager=transcript_manager,
            config=config,
            tools=tools_schema,
            filler_engine=filler_engine,
            delivery=delivery,
        )
        teardown_interceptor = TeardownInterceptor(
            transcript_mgr=transcript_manager,
            delivery=delivery,
        )
        # Let the deterministic farewell backstop arm a teardown for the current reply.
        adapter_processor.teardown = teardown_interceptor

        # Task state tags flow to this session's transcript manager.
        task_broker.bind_transcript_manager(transcript_manager)
        # Proactive speech flows through this session's pipeline once the
        # wakeword arrives (AdapterProcessor binds the inject sink then).

        async def handle_query_hermes(params: FunctionCallParams):
            prompt = params.args.get("prompt", "")
            logger.info(f"Dispatching Hermes run for prompt: {prompt}")

            # No dispatch filler clip by design — the model's own spoken
            # handoff ("Let me check...") is the acknowledgement; the user can
            # keep talking and the result is spoken when ready. dispatch()
            # never raises: a failed dispatch returns a `failed` task with a
            # spoken failure notice already queued.
            task = await task_broker.dispatch(prompt, session_id=voice_session_id)
            if task.state == HermesTaskState.FAILED:
                return {"status": "dispatch_failed", "task_id": task.task_id}
            return {"status": "task_dispatched_to_background", "task_id": task.task_id}

        llm.register_function("query_hermes_agent", handle_query_hermes)

        # Build pipeline
        pipeline = build_pipeline(
            transport=transport,
            vad_processor=vad_service.processor,
            stt_service=stt_service,
            adapter_processor=adapter_processor,
            llm_service=llm,
            teardown_interceptor=teardown_interceptor,
            tts_service=tts_service
        )

        task = PipelineTask(pipeline, enable_rtvi=False)

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, websocket):
            logger.info("Client disconnected, cancelling pipeline task")
            await task.cancel()

        runner = PipelineRunner()
        await runner.add_workers(task)

        try:
            await runner.run()
        except Exception as e:
            logger.error(f"Error running pipeline task: {e}", exc_info=True)
        finally:
            # Undelivered results are retained in the app-level coordinator.
            delivery.unbind_session()
            task_broker.bind_transcript_manager(None)
            logger.info("WebSocket session terminated")
