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

    try:
        async with _session_lock:
            await websocket.accept()
            logger.info("WebSocket connection accepted")
            
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect(message.get("code", 1000))
                
                # Skeleton: log incoming messages
                bytes_data = message.get("bytes")
                text_data = message.get("text")
                
                if bytes_data is not None:
                    logger.debug(f"Received {len(bytes_data)} bytes of binary data")
                elif text_data is not None:
                    logger.info(f"Received text message: {text_data}")
                    try:
                        payload = json.loads(text_data)
                        await websocket.send_json({
                            "type": "status",
                            "state": "received",
                            "echo": payload
                        })
                    except json.JSONDecodeError:
                        pass
                        
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
    finally:
        logger.info("WebSocket connection closed")
