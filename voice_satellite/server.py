"""
FastAPI server orchestrator for the Voice Satellite, managing routing, static files, and WebSockets.
"""

import asyncio
import json
import logging
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from voice_satellite.session import SessionState, ConversationSession

logger = logging.getLogger("voice_satellite.server")

app = FastAPI()

ROOT_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT_DIR / "static"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Module-level lock for enforcing single active WebSocket session
_session_lock = asyncio.Lock()

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

    try:
        async with _session_lock:
            await websocket.accept()
            logger.info("WebSocket connection accepted")

            # Set up the callback to send state updates over WebSocket
            def on_state_change(old_state: SessionState, new_state: SessionState) -> None:
                asyncio.create_task(websocket.send_json({
                    "type": "status",
                    "state": new_state.value
                }))
                if new_state == SessionState.ACTIVE_LISTENING:
                    session.start_silence_timer()
                else:
                    session.cancel_silence_timer()

            session.set_state_change_callback(on_state_change)

            def on_silence_expire() -> None:
                logger.info("Silence timeout expired — returning to passive listening")
                session.state = SessionState.PASSIVE_LISTENING

            session.set_silence_callback(on_silence_expire)

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
                    # On binary PCM message, when in active_listening, store audio buffer
                    if session.state == SessionState.ACTIVE_LISTENING:
                        session.audio_buffer = bytes_data
                        logger.info(f"Stored {len(bytes_data)} bytes of audio data in session")
                        session.reset_silence_timer()
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
                            # Simulate acknowledgment delay
                            await asyncio.sleep(0.5)
                            session.state = SessionState.ACTIVE_LISTENING
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
        logger.info("WebSocket connection closed")
