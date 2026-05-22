"""
FastAPI server orchestrator for the Voice Satellite, managing routing, static files, and WebSockets.

Architecture (Phase 8):
- All transcripts are routed directly to the configured OpenClaw agent (gateway_agent_id)
  via a persistent WebSocket connection established at startup.
- LLMRouter and client-side TaskTracker have been removed.
- Barge-in uses a two-part strategy: sessions.abort on the gateway + a conditional
  one-shot context note prepended to the next user message (≥10 words heard threshold).
"""

import asyncio
import base64
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from voice_satellite.config import load_config
from voice_satellite.stt.whisper_stt import WhisperSTT
from voice_satellite.tts.genie_client import GenieTTSClient
from voice_satellite.tts.sentence_splitter import split_sentences
from voice_satellite.audio.filler_engine import FillerEngine
from voice_satellite.gateway.base import GatewayClient
from voice_satellite.gateway.openclaw_client import OpenClawClient
from voice_satellite.gateway.hermes_client import HermesClient
from voice_satellite.session import SessionState, ConversationSession
from voice_satellite.audio.effects import apply_effect_chain, get_character_effects_config

logger = logging.getLogger("voice_satellite.server")

ROOT_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT_DIR / "static"

# Module-level lock for enforcing single active WebSocket session
_session_lock = asyncio.Lock()

# Persistent session key used for all chat interactions with the gateway
_GATEWAY_SESSION_KEY = "voice"


def get_gateway_client(config) -> GatewayClient:
    """
    Factory to instantiate either HermesClient or OpenClawClient based on config.
    """
    if config.gateway_backend == "openclaw":
        return OpenClawClient(
            gateway_url=config.gateway_url,
            gateway_token=config.gateway_token,
            min_protocol=config.gateway_min_protocol,
            max_protocol=config.gateway_max_protocol
        )
    else:
        return HermesClient(base_url=config.hermes_base_url)


@asynccontextmanager
async def lifespan(app_: FastAPI):
    # Load config
    config = load_config()
    app_.state.config = config

    # Initialize STT
    app_.state.stt = WhisperSTT(
        model_name=config.whisper_model,
        language=config.whisper_language
    )

    # Initialize TTS
    degraded = not (config.tts_onnx_model_dir and config.tts_reference_audio and config.tts_reference_text)
    app_.state.tts = GenieTTSClient(
        tts_url=config.tts_url,
        character_name=config.tts_character_name,
        onnx_model_dir=config.tts_onnx_model_dir,
        reference_audio=config.tts_reference_audio,
        reference_text=config.tts_reference_text,
        language=config.tts_language,
        degraded_mode=degraded
    )
    if not config.skip_genie_init and not degraded:
        await app_.state.tts.load_character()

    # Initialize filler engine
    app_.state.filler_engine = FillerEngine(filler_dir=config.filler_dir)
    app_.state.filler_engine.load_fillers()

    # Initialize persistent gateway client and attempt connection
    app_.state.gateway_client = get_gateway_client(config)
    app_.state.openclaw_client = app_.state.gateway_client
    try:
        await app_.state.gateway_client.connect()
        if config.gateway_backend == "openclaw":
            logger.info(
                "OpenClaw gateway connected (agent: %s, protocol: v%s)",
                config.gateway_agent_id,
                getattr(app_.state.gateway_client, "protocol", "unknown"),
            )
        else:
            logger.info(
                "Hermes gateway connected (base_url: %s, session_id: %s)",
                config.hermes_base_url,
                app_.state.gateway_client.session_id,
            )
    except Exception as exc:
        logger.warning("%s gateway unavailable at startup: %s — degraded mode", config.gateway_backend.upper(), exc)

    try:
        yield
    finally:
        app_.state.stt.close()
        await app_.state.gateway_client.close()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> HTMLResponse:
    """Serves the index.html page or a fallback message if it doesn't exist."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Voice Satellite Core Client</h1>")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for speech ingestion and audio/status streaming.
    Enforces a single concurrent session policy (FR-007a).
    """
    if _session_lock.locked():
        logger.warning("Rejecting connection: session already active")
        await websocket.accept()
        await websocket.send_json({
            "type": "error",
            "message": "Session already active. Please wait."
        })
        await websocket.close(code=1008)  # Policy Violation close code
        return

    session = ConversationSession()
    ws_lock = asyncio.Lock()

    config = getattr(app.state, "config", None)
    if not config:
        config = load_config()

    # Per-session barge-in context: stores partial response text from last interrupt.
    # Reset to "" after it has been consumed on the next outgoing message.
    _interrupted_partial: list[str] = [""]  # mutable cell for closure capture

    try:
        async with _session_lock:
            await websocket.accept()
            logger.info("WebSocket connection accepted")

            # Set up the callback to send state updates over WebSocket
            def on_state_change(old_state: SessionState, new_state: SessionState) -> None:
                async def send_state():
                    async with ws_lock:
                        await websocket.send_json({
                            "type": "status",
                            "state": new_state.value
                        })
                asyncio.create_task(send_state())
                if new_state == SessionState.ACTIVE_LISTENING:
                    session.start_silence_timer()
                else:
                    session.cancel_silence_timer()

            session.set_state_change_callback(on_state_change)

            def on_silence_expire() -> None:
                logger.info("Silence timeout expired — returning to passive listening")
                session.state = SessionState.PASSIVE_LISTENING

            session.set_silence_callback(on_silence_expire)

            async def send_filler(category: str) -> None:
                filler_engine = getattr(app.state, "filler_engine", None)
                if not filler_engine:
                    return
                pcm = filler_engine.get_filler(category)
                if pcm:
                    async with ws_lock:
                        await websocket.send_json({
                            "type": "audio",
                            "data": base64.b64encode(pcm).decode("ascii"),
                            "word_offset": 0,
                            "sample_rate": 32000
                        })

            async def speak_text_to_tts(text: str) -> None:
                chunks = split_sentences(text)
                if not chunks:
                    return

                session.set_current_response(text)

                async with ws_lock:
                    await websocket.send_json({"type": "assistant_response", "text": text})

                tts_client = getattr(app.state, "tts", None)
                if tts_client and getattr(tts_client, "degraded_mode", False) is True and getattr(tts_client, "onnx_model_dir", None):
                    logger.info("TTS is in degraded mode. Retrying character load...")
                    tts_client.degraded_mode = False
                    try:
                        await tts_client.load_character()
                    except TypeError:
                        pass

                # If degraded_mode is still truthy (which includes MagicMocks that aren't boolean False)
                is_degraded = getattr(tts_client, "degraded_mode", False)
                if not tts_client or (is_degraded and is_degraded is not False):
                    async with ws_lock:
                        await websocket.send_json({"type": "audio_end"})
                    return

                first_chunk = chunks[0]
                first_iter = tts_client.synthesize(first_chunk.text)

                async def get_next_item(it):
                    try:
                        return await it.__anext__()
                    except StopAsyncIteration:
                        return None

                first_chunk_task = asyncio.create_task(get_next_item(first_iter))

                done, pending = await asyncio.wait(
                    [first_chunk_task],
                    timeout=config.filler_threshold_secs
                )

                first_audio = None
                if first_chunk_task in done:
                    first_audio = first_chunk_task.result()
                else:
                    logger.info("TTS synthesis latency exceeded threshold, playing filler")
                    session.state = SessionState.FILLER_SPEAKING
                    filler_engine = getattr(app.state, "filler_engine", None)
                    if filler_engine:
                        filler_pcm = filler_engine.get_filler("thinking")
                        if filler_pcm:
                            async with ws_lock:
                                await websocket.send_json({
                                    "type": "audio",
                                    "data": base64.b64encode(filler_pcm).decode("ascii"),
                                    "word_offset": 0,
                                    "sample_rate": 32000
                                })
                    first_audio = await first_chunk_task

                session.state = SessionState.SPEAKING

                current_word_offset = 0
                effects_config = get_character_effects_config(config.tts_character_name)
                if first_audio:
                    if first_chunk.tagged:
                        first_audio = apply_effect_chain(first_audio, effects_config)
                    async with ws_lock:
                        await websocket.send_json({
                            "type": "audio",
                            "data": base64.b64encode(first_audio).decode("ascii"),
                            "word_offset": current_word_offset,
                            "sample_rate": 32000
                        })

                async for audio_chunk in first_iter:
                    if audio_chunk:
                        if first_chunk.tagged:
                            audio_chunk = apply_effect_chain(audio_chunk, effects_config)
                        async with ws_lock:
                            await websocket.send_json({
                                "type": "audio",
                                "data": base64.b64encode(audio_chunk).decode("ascii"),
                                "word_offset": current_word_offset,
                                "sample_rate": 32000
                            })

                current_word_offset += len(first_chunk.text.split())

                for chunk in chunks[1:]:
                    async for audio_chunk in tts_client.synthesize(chunk.text):
                        if audio_chunk:
                            if chunk.tagged:
                                audio_chunk = apply_effect_chain(audio_chunk, effects_config)
                            async with ws_lock:
                                await websocket.send_json({
                                    "type": "audio",
                                    "data": base64.b64encode(audio_chunk).decode("ascii"),
                                    "word_offset": current_word_offset,
                                    "sample_rate": 32000
                                })
                    current_word_offset += len(chunk.text.split())

                async with ws_lock:
                    await websocket.send_json({"type": "audio_end"})

            def _build_outgoing_message(transcript: str) -> str:
                """
                Builds the outgoing message for OpenClaw, prepending a one-shot barge-in
                context note if the last response was interrupted with ≥10 words heard.
                The context note is consumed and cleared after use.
                """
                partial = _interrupted_partial[0]
                _interrupted_partial[0] = ""  # consume

                if not partial:
                    return transcript

                word_count = len(partial.split())
                if word_count < 10:
                    # Short barge-in: gateway history already has the truncated turn.
                    # No extra context needed.
                    return transcript

                # Long barge-in: prepend a one-shot context note (not stored in gateway history).
                context_note = (
                    f"[System Note: The last assistant response was interrupted by the user "
                    f'after saying: "{partial} [interrupted]"]\n'
                )
                return context_note + transcript

            async def handle_audio(audio_data: bytes) -> None:
                try:
                    session.state = SessionState.TRANSCRIBING

                    stt_client = getattr(app.state, "stt", None)
                    if stt_client:
                        transcript = await stt_client.transcribe(audio_data)
                    else:
                        transcript = ""

                    if not transcript:
                        session.state = SessionState.ACTIVE_LISTENING
                        return

                    session.last_transcript = transcript
                    session.reset_silence_timer()

                    async with ws_lock:
                        await websocket.send_json({"type": "transcript", "text": transcript})
                        await websocket.send_json({"type": "status", "state": "thinking"})

                    session.state = SessionState.THINKING

                    # Build outgoing message — may prepend barge-in context note
                    outgoing_message = _build_outgoing_message(transcript)

                    # Route directly to the configured OpenClaw agent
                    openclaw_client: OpenClawClient = app.state.openclaw_client
                    run_id = await openclaw_client.send_message(
                        agent_id=config.gateway_agent_id,
                        message=outgoing_message,
                        mode="persistent",
                        session_key=_GATEWAY_SESSION_KEY,
                    )

                    # Stream response tokens → sentence splitter → TTS → audio chunks
                    response_chunks: list[str] = []
                    sentence_buffer = ""

                    async for token in openclaw_client.stream_response(run_id):
                        if not token:
                            continue
                        response_chunks.append(token)
                        sentence_buffer += token

                        # Flush complete sentences to TTS as they arrive
                        sentences = split_sentences(sentence_buffer)
                        if len(sentences) > 1:
                            # All sentences except the last (which may be incomplete)
                            for s in sentences[:-1]:
                                await speak_text_to_tts(s.text)
                            # Keep the trailing incomplete sentence in the buffer
                            sentence_buffer = sentences[-1].text if sentences[-1].text else ""

                    # Flush any remaining text after the stream ends
                    if sentence_buffer.strip():
                        await speak_text_to_tts(sentence_buffer)

                    session.state = SessionState.ACTIVE_LISTENING

                except asyncio.CancelledError:
                    logger.info("handle_audio task cancelled")
                    raise
                except Exception as e:
                    logger.error(f"Error in handle_audio: {e}", exc_info=True)
                    try:
                        async with ws_lock:
                            await websocket.send_json({
                                "type": "error",
                                "message": "Error processing audio"
                            })
                    except Exception:
                        pass
                    session.state = SessionState.ACTIVE_LISTENING

            # Send initial state to the client
            await websocket.send_json({
                "type": "status",
                "state": session.state.value
            })

            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect(message.get("code", 1000))

                bytes_data = message.get("bytes")
                text_data = message.get("text")

                if bytes_data is not None:
                    if session.is_passive():
                        logger.debug("Passive mode: discarding audio (no wakeword)")
                        continue

                    # Cancel ongoing generation / stop TTS
                    if session.generation_task and not session.generation_task.done():
                        partial = await session.cancel_generation()
                        if partial:
                            # Part 1 of barge-in: abort the active gateway run (best-effort)
                            try:
                                openclaw_client = app.state.openclaw_client
                                await openclaw_client.sessions_abort(
                                    session_key=f"agent:{config.gateway_agent_id}:{_GATEWAY_SESSION_KEY}"
                                )
                            except Exception as exc:
                                logger.warning("sessions_abort failed (non-fatal): %s", exc)
                            # Store partial for context note on next turn
                            _interrupted_partial[0] = partial

                        tts_client = getattr(app.state, "tts", None)
                        if tts_client:
                            await tts_client.stop()

                    generation_task = asyncio.create_task(handle_audio(bytes_data))
                    session.set_generation_task(generation_task)

                elif text_data is not None:
                    try:
                        payload = json.loads(text_data)
                    except json.JSONDecodeError:
                        payload = {}

                    msg_type = payload.get("type", "")

                    if msg_type == "wakeword":
                        if session.is_passive():
                            logger.info("Wakeword received — transitioning to acknowledging")
                            session.state = SessionState.ACKNOWLEDGING
                            await send_filler("acknowledge")
                            session.state = SessionState.ACTIVE_LISTENING

                    elif msg_type == "interrupt":
                        # Barge-in via explicit interrupt message (frontend VAD detected speech)
                        partial = await session.cancel_generation()
                        if partial:
                            # Part 1: abort the active gateway run
                            try:
                                openclaw_client = app.state.openclaw_client
                                await openclaw_client.sessions_abort(
                                    session_key=f"agent:{config.gateway_agent_id}:{_GATEWAY_SESSION_KEY}"
                                )
                            except Exception as exc:
                                logger.warning("sessions_abort failed (non-fatal): %s", exc)
                            _interrupted_partial[0] = partial

                        tts_client = getattr(app.state, "tts", None)
                        if tts_client:
                            await tts_client.stop()

                        session.state = SessionState.ACTIVE_LISTENING
                        session.reset_silence_timer()
                        async with ws_lock:
                            await websocket.send_json({"type": "flush_audio"})
                            await websocket.send_json({"type": "status", "state": "interrupted"})
                            await websocket.send_json({"type": "status", "state": "active_listening"})

                    elif msg_type == "playback_progress":
                        words_played = int(payload.get("words_played", 0))
                        session.update_words_played(words_played)

                    elif msg_type == "set_timeout":
                        seconds = float(payload.get("seconds", 30.0))
                        seconds = max(10.0, min(120.0, seconds))
                        session.silence_timeout = seconds
                        logger.info(f"Updated session silence timeout to {seconds}s")
                        if session.state == SessionState.ACTIVE_LISTENING:
                            session.reset_silence_timer()

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
    finally:
        await session.close()
        tts_client = getattr(app.state, "tts", None)
        if tts_client:
            try:
                await tts_client.stop()
            except Exception:
                pass
        logger.info("WebSocket connection closed")
