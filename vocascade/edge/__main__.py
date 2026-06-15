"""
vocascade/edge/__main__.py — Edge/satellite client (US8; reshaped from satellite.py).

Runs the *edge* half of the split topology (FR-110): wake word + VAD + audio I/O
+ the pipeline client over a WebSocket. STT, the waterfall, the local LLM, TTS
and Hermes all live on the *server*. Hosts come from config/`.env` — no
hardcoded addresses.

Transport auth (OQ-3): in ``device-identity`` mode the client completes an
Ed25519 challenge-response (sign the server's nonce with the device key) before
any audio flows; ``trust-network`` connects straight through. When the server is
unreachable or the link drops mid-utterance, the client surfaces a clear status
and returns to listening (FR-102) — it does not hang.

Run it with::

    .venv/bin/python -m vocascade.edge

Heavy capture deps (pyaudio, openwakeword, numpy) are imported lazily so the
connection/handshake logic stays importable and unit-testable without a mic.
"""

import asyncio
import json
import logging
import os
import time
from enum import Enum

import websockets

from vocascade.gateway.auth import (
    load_or_generate_keypair,
    sign_challenge,
    public_key_to_b64,
)

logger = logging.getLogger("vocascade.edge")

TRUST_NETWORK = "trust-network"
DEVICE_IDENTITY = "device-identity"


class AuthError(Exception):
    """The transport handshake was rejected or violated the protocol."""


class ClientState(Enum):
    LISTENING = "LISTENING"
    CONNECTING = "CONNECTING"
    STREAMING = "STREAMING"


async def perform_client_handshake(ws, mode: str, identity_key_path: str,
                                   timeout: float = 5.0) -> bool:
    """
    Edge side of the transport auth handshake (OQ-3).

    ``trust-network``   → no-op (the network boundary is the perimeter).
    ``device-identity`` → receive the server's ``auth_challenge`` nonce, sign it
    with the device key (created on first run), present the public key, and await
    ``auth_ok``.

    Returns True on success; raises ``AuthError`` on rejection/protocol error.
    Works against any object exposing async ``send`` / ``recv`` (a ``websockets``
    connection or a test double).
    """
    if mode == TRUST_NETWORK:
        return True
    if mode != DEVICE_IDENTITY:
        raise AuthError(f"unknown transport_auth_mode {mode!r}")

    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    except asyncio.TimeoutError as e:
        raise AuthError("timed out waiting for the server challenge") from e
    challenge = json.loads(raw)
    if challenge.get("type") != "auth_challenge" or not challenge.get("nonce"):
        raise AuthError(f"expected auth_challenge, got {challenge.get('type')!r}")

    private_key, public_key = load_or_generate_keypair(identity_key_path)
    await ws.send(json.dumps({
        "type": "auth_response",
        "public_key": public_key_to_b64(public_key),
        "signature": sign_challenge(private_key, challenge["nonce"]),
    }))

    try:
        reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
    except asyncio.TimeoutError as e:
        raise AuthError("timed out waiting for the auth result") from e
    if reply.get("type") != "auth_ok":
        raise AuthError(reply.get("message", "authentication rejected by server"))
    logger.info("Device identity accepted by server")
    return True


class SatelliteClient:
    def __init__(self, config: dict):
        self.config = config
        self.state = ClientState.LISTENING
        self.ws = None

        # Transport auth (OQ-3) — driven by config, never an accidental open link.
        self.auth_mode = config.get("transport_auth_mode", TRUST_NETWORK)
        self.identity_key_path = config.get("identity_key_path", "~/.vocascade/identity.pem")

        # Audio config
        self.chunk_size = 1280  # Suitable chunk size for openWakeWord
        self.audio_in_rate = config.get("audio_in_rate", 16000)
        self.audio_out_rate = config.get("audio_out_rate", 32000)

        self.p = None
        self.stream_in = None
        self.stream_out = None
        self.oww_model = None

        self.ws_url = config.get("ws_url", "ws://localhost:8000/ws")
        self.last_audio_time = time.time()

    def _set_status(self, message: str):
        """Surface a clear edge status (FR-102). Logged today; a UI/LED hook can
        subscribe here without touching the connection logic."""
        logger.info("[edge status] %s", message)

    def start_audio(self):
        import pyaudio
        from openwakeword.model import Model

        self.p = pyaudio.PyAudio()
        try:
            self.stream_in = self.p.open(
                format=pyaudio.paInt16, channels=1, rate=self.audio_in_rate,
                input=True, frames_per_buffer=self.chunk_size,
            )
            self.stream_out = self.p.open(
                format=pyaudio.paInt16, channels=1, rate=self.audio_out_rate,
                output=True, frames_per_buffer=self.chunk_size,
            )
            logger.info("Audio streams initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize audio streams: {e}")

        model_path = self.config.get("wake_word_model")
        if model_path and os.path.exists(model_path):
            self.oww_model = Model(wakeword_model_paths=[model_path])
            logger.info(f"Loaded wake word model: {model_path}")
        else:
            self.oww_model = None
            logger.warning(f"Wake word model not found at {model_path}. Wake word detection disabled.")

    def stop_audio(self):
        if self.stream_in:
            self.stream_in.stop_stream()
            self.stream_in.close()
        if self.stream_out:
            self.stream_out.stop_stream()
            self.stream_out.close()
        if self.p:
            self.p.terminate()

    async def handle_wake_word_detected(self):
        logger.info("Wake word detected! Transitioning to CONNECTING.")
        self.state = ClientState.CONNECTING
        await self._connect_ws()

    async def _connect_ws(self):
        try:
            self.ws = await websockets.connect(self.ws_url)
        except (OSError, websockets.exceptions.WebSocketException) as e:
            # FR-102: server unreachable — surface a clear status, do not hang.
            self._set_status(f"Server unreachable at {self.ws_url} ({e}); staying in listening mode.")
            self.state = ClientState.LISTENING
            self.ws = None
            return

        try:
            await perform_client_handshake(self.ws, self.auth_mode, self.identity_key_path)
        except AuthError as e:
            self._set_status(f"Transport authentication failed: {e}. Not streaming.")
            await self._close_ws()
            self.state = ClientState.LISTENING
            return
        except Exception as e:
            self._set_status(f"Handshake error: {e}. Not streaming.")
            await self._close_ws()
            self.state = ClientState.LISTENING
            return

        self.state = ClientState.STREAMING
        self.last_audio_time = time.time()
        logger.info("Connected to server, transitioning to STREAMING.")
        asyncio.create_task(self._ws_receive_loop())
        asyncio.create_task(self._ws_send_loop())

    async def _close_ws(self):
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None

    async def _disconnect_ws(self):
        self.state = ClientState.LISTENING
        await self._close_ws()
        logger.info("Disconnected from server. Returning to LISTENING.")

    async def handle_silence_timeout(self):
        logger.info("Silence timeout reached. Disconnecting.")
        await self._disconnect_ws()

    async def handle_server_close(self):
        # FR-102: a mid-utterance partition surfaces a status and recovers.
        self._set_status("Connection to server lost; returning to listening mode.")
        await self._disconnect_ws()

    async def _ws_receive_loop(self):
        try:
            while self.state == ClientState.STREAMING and self.ws:
                message = await self.ws.recv()
                if isinstance(message, bytes):
                    if self.stream_out:
                        await asyncio.to_thread(self.stream_out.write, message)
                self.last_audio_time = time.time()
        except websockets.exceptions.ConnectionClosed:
            await self.handle_server_close()
        except Exception as e:
            logger.error(f"Error in receive loop: {e}")
            await self.handle_server_close()

    async def _ws_send_loop(self):
        try:
            CHUNK_SAMPLES = 4096          # 256ms at 16kHz
            while self.state == ClientState.STREAMING and self.ws:
                if self.stream_in:
                    data = await asyncio.to_thread(self.stream_in.read, CHUNK_SAMPLES, False)
                    if self.state == ClientState.STREAMING and self.ws:
                        await self.ws.send(data)
                if time.time() - self.last_audio_time > 15.0:
                    await self.handle_silence_timeout()
                    break
        except websockets.exceptions.ConnectionClosed:
            await self.handle_server_close()
        except Exception as e:
            logger.error(f"Error in send loop: {e}")
            await self.handle_server_close()

    async def run_loop(self):
        import numpy as np

        self.start_audio()
        logger.info("Starting satellite listening loop (auth=%s)...", self.auth_mode)
        try:
            while True:
                if self.state == ClientState.LISTENING:
                    if not self.stream_in:
                        await asyncio.sleep(1)
                        continue

                    pcm = await asyncio.to_thread(self.stream_in.read, self.chunk_size, False)
                    if self.oww_model:
                        audio_data = np.frombuffer(pcm, dtype=np.int16)
                        self.oww_model.predict(audio_data)
                        for mdl in self.oww_model.prediction_buffer.keys():
                            scores = list(self.oww_model.prediction_buffer[mdl])
                            if scores and scores[-1] > 0.5:
                                self.oww_model.reset()
                                await self.handle_wake_word_detected()
                                break
                    await asyncio.sleep(0.01)
                else:
                    await asyncio.sleep(0.1)
        except KeyboardInterrupt:
            logger.info("Stopping satellite client...")
        finally:
            self.stop_audio()
            await self._close_ws()


def _load_edge_config() -> dict:
    """Assemble edge config from config.yaml (auth posture) + .env (hosts/paths)."""
    from dotenv import load_dotenv
    load_dotenv()

    auth_mode = TRUST_NETWORK
    identity_key_path = os.path.expanduser(
        os.getenv("EDGE_IDENTITY_KEY_PATH", "~/.vocascade/identity.pem"))
    try:
        from vocascade.config import load_config
        cfg = load_config()
        auth_mode = cfg.transport_auth_mode
        identity_key_path = cfg.identity_key_path
    except Exception as e:
        logger.warning("Falling back to env-only edge config (%s)", e)

    return {
        "ws_url": os.getenv("WS_URL", "ws://localhost:8000/ws"),
        "wake_word_model": os.getenv("WAKE_WORD_MODEL", "static/wakeword/eden_wakeword.onnx"),
        "audio_in_rate": int(os.getenv("AUDIO_IN_SAMPLE_RATE", "16000")),
        "audio_out_rate": int(os.getenv("AUDIO_OUT_SAMPLE_RATE", "32000")),
        "transport_auth_mode": auth_mode,
        "identity_key_path": identity_key_path,
    }


def main():
    logging.basicConfig(level=logging.INFO)
    client = SatelliteClient(_load_edge_config())
    asyncio.run(client.run_loop())


if __name__ == "__main__":
    main()
