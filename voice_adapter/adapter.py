"""
Core module for the Pipecat Voice Adapter.
Defines the FastAPI app, the WebSocket endpoint, the RawFrameSerializer,
the SileroVADService shim, and the AdapterProcessor.
"""

import asyncio
import datetime
import logging
from contextlib import asynccontextmanager
from typing import List, Optional

from pathlib import Path
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

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
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response import LLMFullResponseAggregator
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.whisper.stt import WhisperSTTService, WhisperSTTSettings
from pipecat.transcriptions.language import Language
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams

from voice_adapter.config import AdapterConfig, load_config
from voice_adapter.transcript_manager import TranscriptManager, TranscriptTurn
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


class RawFrameSerializer(FrameSerializer):
    """Serializer converting raw PCM audio bytes to/from Pipecat raw audio frames."""

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, OutputAudioRawFrame):
            return frame.audio
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, bytes):
            return InputAudioRawFrame(
                audio=data,
                sample_rate=16000,
                num_channels=1
            )
        return None


class SileroVADService:
    """Shim class satisfying SileroVADService requirements by wrapping Pipecat's VADProcessor."""
    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.analyzer = SileroVADAnalyzer(sample_rate=sample_rate)
        self.processor = VADProcessor(vad_analyzer=self.analyzer)


class AdapterProcessor(FrameProcessor):
    """
    Custom FrameProcessor representing the Voice Adapter logic.
    In Phase 3 MVP, it manages the sliding-window conversation history,
    injects a minimal system prompt, and intercepts user transcriptions
    to route them directly to the Qwen LLM via OpenAILLMService.
    """

    def __init__(self, transcript_manager: TranscriptManager, config: AdapterConfig):
        super().__init__()
        self.transcript_manager = transcript_manager
        self.config = config
        self.system_message = "You are a helpful voice assistant. Keep your responses concise and brief."

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
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

        elif isinstance(frame, TranscriptionFrame):
            # 1. Append user transcription to TranscriptManager
            user_turn = TranscriptTurn(
                role="user",
                content=frame.text,
            )
            self.transcript_manager.append(user_turn)

            # 2. Reconstruct the full message list from the sliding window
            turns = self.transcript_manager.get_window()
            messages = []
            for turn in turns:
                messages.append({
                    "role": turn.role,
                    "content": turn.content
                })

            # Create the LLMContext and wrap it in an LLMContextFrame
            context = LLMContext(messages=messages)
            context_frame = LLMContextFrame(context=context)

            # 3. Push LLMContextFrame downstream
            await self.push_frame(context_frame, direction)
            return

        # Propagate all other frames
        await self.push_frame(frame, direction)


def build_pipeline(
    transport: FastAPIWebsocketTransport,
    vad_processor: VADProcessor,
    stt_service: WhisperSTTService,
    adapter_processor: AdapterProcessor,
    llm_service: OpenAILLMService,
    response_aggregator: LLMFullResponseAggregator,
    tts_service: GenieTTSService,
) -> Pipeline:
    """Assembles and returns the core Pipecat pipeline."""
    return Pipeline([
        transport.input(),
        vad_processor,
        stt_service,
        adapter_processor,
        llm_service,
        response_aggregator,
        tts_service,
        transport.output(),
    ])


@asynccontextmanager
async def lifespan(app_: FastAPI):
    # Load config
    config = load_config()
    app_.state.config = config

    # Initialize Whisper STT Service
    logger.info("Initializing Whisper STT service...")
    app_.state.stt_service = WhisperSTTService(
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
        await app_.state.tts_service.start()

    yield

    # Cleanup resources
    logger.info("Cleaning up services...")
    if hasattr(app_.state, "tts_service"):
        await app_.state.tts_service.stop()


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

        llm = OpenAILLMService(
            api_key=config.hermes_api_key or "not-needed",
            base_url=config.hermes_base_url,
            model=config.hermes_model
        )

        transcript_manager = TranscriptManager()
        adapter_processor = AdapterProcessor(transcript_manager=transcript_manager, config=config)

        response_aggregator = LLMFullResponseAggregator()

        @response_aggregator.event_handler("on_completion")
        async def on_llm_completion(aggregator, completion: str, completed: bool):
            if completed and completion:
                transcript_manager.append(TranscriptTurn(role="assistant", content=completion))

        # Build pipeline
        pipeline = build_pipeline(
            transport=transport,
            vad_processor=vad_service.processor,
            stt_service=stt_service,
            adapter_processor=adapter_processor,
            llm_service=llm,
            response_aggregator=response_aggregator,
            tts_service=tts_service
        )

        task = PipelineTask(pipeline, enable_rtvi=False)

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, websocket):
            logger.info("Client disconnected, cancelling pipeline task")
            await task.cancel()

        runner = PipelineRunner()
        runner.add_workers(task)

        try:
            await runner.run()
        except Exception as e:
            logger.error(f"Error running pipeline task: {e}", exc_info=True)
        finally:
            logger.info("WebSocket session terminated")
