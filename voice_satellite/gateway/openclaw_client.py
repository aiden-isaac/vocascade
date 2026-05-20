"""
WebSocket client for the OpenClaw multi-agent gateway.
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator
import websockets
from cryptography.hazmat.primitives import serialization

from voice_satellite.gateway.auth import load_or_generate_keypair, sign_challenge

logger = logging.getLogger("voice_satellite.gateway")

class OpenClawClient:
    """
    Client for OpenClaw gateway using WebSockets.
    Supports min/max protocol negotiation, device identity signing,
    reconnections, and streaming agent/chat interactions.
    """
    def __init__(
        self,
        gateway_url: str,
        gateway_token: str,
        device_key_path: str | Path | None = None,
        min_protocol: int = 3,
        max_protocol: int = 4,
        connect_impl: Any = None
    ) -> None:
        self.gateway_url = gateway_url
        self.gateway_token = gateway_token
        self.min_protocol = min_protocol
        self.max_protocol = max_protocol
        self.connect_impl = connect_impl or websockets.connect

        if device_key_path is None:
            self.device_key_path = Path(os.path.expanduser("~")) / ".openclaw" / "identity" / "device.json"
        else:
            self.device_key_path = Path(device_key_path)

        self._websocket: Any | None = None
        self._pending_frames: list[dict[str, Any]] = []
        self._degraded = False

        # Track last request context for streaming
        self.last_run_id = None
        self.last_event_name = None
        self.last_session_key = None
        self.protocol = None

    @property
    def degraded_mode(self) -> bool:
        """
        Returns True if the client is running without device identity authentication.
        """
        return self._degraded

    async def connect(self) -> None:
        """
        Establishes WebSocket connection and runs challenge-response handshake.
        """
        logger.info(f"Connecting to OpenClaw gateway at {self.gateway_url}")
        self._websocket = await self.connect_impl(self.gateway_url)

        # 1. Receive challenge
        try:
            challenge_frame = await asyncio.wait_for(self._recv_frame(), timeout=10.0)
        except Exception as e:
            logger.error("Failed to receive handshake challenge from gateway")
            await self.close()
            raise ConnectionError("Handshake timeout/failure waiting for challenge") from e

        if challenge_frame.get("type") == "event" and challenge_frame.get("event") == "connect.challenge":
            nonce = challenge_frame["payload"]["nonce"]
        else:
            logger.warning(f"Unexpected initial frame from gateway: {challenge_frame}")
            nonce = None

        # 2. Build parameters with device auth and send connect request
        req_id = self._make_id("req")
        params = self._connect_params(nonce)

        await self._send_json({
            "type": "req",
            "id": req_id,
            "method": "connect",
            "params": params
        })

        # 3. Wait for hello-ok/connect response
        try:
            res_frame = await asyncio.wait_for(self._recv_frame(), timeout=10.0)
        except Exception as e:
            logger.error("Failed to receive handshake response from gateway")
            await self.close()
            raise ConnectionError("Handshake timeout/failure waiting for response") from e

        if res_frame.get("type") == "res" and res_frame.get("id") == req_id:
            if not res_frame.get("ok"):
                error_info = res_frame.get("error", {})
                code = error_info.get("code", "unknown")
                msg = error_info.get("message", "auth failed")
                raise ConnectionError(f"Gateway connection rejected: {code} - {msg}")
            self.protocol = res_frame.get("payload", {}).get("protocol") or self.max_protocol
        else:
            raise ConnectionError(f"Unexpected frame instead of handshake response: {res_frame}")

        logger.info("Successfully connected and authenticated with OpenClaw gateway")

    async def close(self) -> None:
        """
        Closes the active WebSocket connection.
        """
        if self._websocket is not None:
            await self._websocket.close()
            self._websocket = None

    async def ensure_connected(self) -> None:
        """
        Ensures active connection, retrying with exponential backoff on failure.
        """
        if self._websocket is not None:
            return

        backoff = 1.0
        while True:
            try:
                await self.connect()
                return
            except Exception as e:
                logger.warning(f"Failed to connect to gateway: {e}. Retrying in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 60.0)

    async def test_connectivity(self) -> bool:
        """
        Verifies gateway connection and authentication.
        """
        try:
            await self.connect()
            await self.close()
            return True
        except Exception as e:
            logger.warning(f"Connectivity test failed: {e}")
            return False

    async def send_message(
        self,
        agent_id: str,
        message: str,
        mode: str = "one-shot",
        session_key: str | None = None
    ) -> str:
        """
        Initiates a message transaction, waiting for the gateway acknowledgment.
        Returns the run ID.
        """
        await self.ensure_connected()

        run_id = self._make_id("voice")
        self.last_run_id = run_id

        if mode == "persistent":
            if not session_key:
                raise ValueError("session_key is required for persistent mode")

            # Clean/resolve session key prefix
            clean_key = session_key
            prefix = f"agent:{agent_id}:"
            if clean_key.startswith(prefix):
                clean_key = clean_key.removeprefix(prefix)
            resolved_session_key = f"{prefix}{clean_key}"

            create_req_id = self._make_id("create-session")
            try:
                await self._request(
                    "sessions.create",
                    {"agentId": agent_id, "key": clean_key, "label": "Voice Satellite"},
                    request_id=create_req_id
                )
            except Exception as e:
                logger.debug(f"sessions.create error (can ignore if already exists): {e}")

            await self._send_json({
                "type": "req",
                "id": run_id,
                "method": "chat.send",
                "params": {
                    "sessionKey": resolved_session_key,
                    "message": message,
                    "deliver": False,
                    "idempotencyKey": run_id
                }
            })
            self.last_event_name = "chat"
            self.last_session_key = resolved_session_key
        else:
            await self._send_json({
                "type": "req",
                "id": run_id,
                "method": "agent",
                "params": {
                    "message": message,
                    "agentId": agent_id,
                    "deliver": False,
                    "idempotencyKey": run_id
                }
            })
            self.last_event_name = "agent"
            self.last_session_key = None

        await self._wait_for_ack(run_id)
        return run_id

    async def stream_response(
        self,
        run_id: str | None = None,
        event_name: str | None = None
    ) -> AsyncIterator[str]:
        """
        Streams response tokens/text chunk-by-chunk from the gateway connection.
        """
        target_run_id = run_id or self.last_run_id
        target_event_name = event_name or self.last_event_name
        target_session_key = self.last_session_key

        if not target_run_id or not target_event_name:
            raise ValueError("No active request transaction to stream")

        accumulated_text = ""

        while True:
            frame = await self._recv_frame()

            if frame.get("type") == "res" and frame.get("ok") is False:
                error_info = frame.get("error", {})
                code = error_info.get("code", "unknown")
                msg = error_info.get("message", "stream failed")
                raise RuntimeError(f"Gateway stream error: {code} - {msg}")

            if frame.get("type") != "event" or frame.get("event") != target_event_name:
                continue

            payload = frame.get("payload") or {}

            # Match runId / sessionKey
            payload_run_id = payload.get("runId") or payload.get("id")
            payload_session_key = payload.get("sessionKey")

            if target_run_id and payload_run_id and payload_run_id != target_run_id:
                continue
            if target_session_key and payload_session_key and payload_session_key != target_session_key:
                continue

            state = self._event_state(payload)
            text = self._extract_text(payload)

            if state in {"aborted", "error"}:
                raise RuntimeError(f"Stream state {state}: {text}")

            if text:
                if accumulated_text and text.startswith(accumulated_text):
                    delta = text[len(accumulated_text):]
                else:
                    delta = text

                if delta:
                    yield delta
                    accumulated_text += delta

            if state == "final":
                return

    async def _wait_for_ack(self, run_id: str) -> None:
        while True:
            frame = await self._recv_frame()
            if frame.get("type") == "res" and frame.get("id") == run_id:
                if not frame.get("ok"):
                    error_info = frame.get("error", {})
                    code = error_info.get("code", "unknown")
                    msg = error_info.get("message", "request failed")
                    raise RuntimeError(f"Gateway request failed: {code} - {msg}")
                break
            self._pending_frames.append(frame)

    def _get_device_info(self) -> tuple[Any, str | None]:
        # Try loading from device.json if it exists
        if self.device_key_path.suffix == ".json" and self.device_key_path.exists():
            try:
                data = json.loads(self.device_key_path.read_text())
                private_key_pem = data.get("privateKeyPem")
                public_key_pem = data.get("publicKeyPem")
                if private_key_pem and public_key_pem:
                    private_key = serialization.load_pem_private_key(
                        private_key_pem.encode("utf-8"), password=None
                    )
                    return private_key, public_key_pem
            except Exception as e:
                logger.error(f"Failed to load device identity from JSON: {e}")

        # Fallback to load/generate PEM key
        pem_path = self.device_key_path
        if pem_path.suffix == ".json":
            pem_path = pem_path.with_suffix(".pem")

        try:
            private_key, public_key = load_or_generate_keypair(pem_path)
            public_key_pem = public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            ).decode("utf-8")
            return private_key, public_key_pem
        except Exception as e:
            logger.error(f"Failed to load/generate device identity PEM: {e}")
            return None, None

    def _build_device_auth(self, nonce: str) -> dict[str, Any] | None:
        private_key, public_key_pem = self._get_device_info()
        if private_key is None:
            logger.warning("No device identity available; skipping device auth")
            self._degraded = True
            return None

        client_id = "cli"
        client_mode = "cli"
        role = "operator"
        scopes_csv = ",".join(["operator.read", "operator.write"])
        signed_at = int(time.time() * 1000)

        # Derive device id from public key bytes SHA256
        raw_pk = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
        import hashlib
        device_id = hashlib.sha256(raw_pk).hexdigest()

        payload_str = f"v2|{device_id}|{client_id}|{client_mode}|{role}|{scopes_csv}|{signed_at}|{self.gateway_token}|{nonce}"
        signature_b64 = sign_challenge(private_key, payload_str)

        return {
            "id": device_id,
            "publicKey": public_key_pem,
            "signature": signature_b64,
            "signedAt": signed_at,
            "nonce": nonce
        }

    def _connect_params(self, nonce: str | None = None) -> dict[str, Any]:
        params = {
            "minProtocol": self.min_protocol,
            "maxProtocol": self.max_protocol,
            "client": {
                "id": "cli",
                "mode": "cli",
                "version": "0.1.0",
                "platform": "linux"
            },
            "role": "operator",
            "scopes": ["operator.read", "operator.write"],
            "auth": {"token": self.gateway_token}
        }
        if nonce:
            device_auth = self._build_device_auth(nonce)
            if device_auth:
                params["device"] = device_auth
                logger.info("Device auth included in connect params")
            else:
                logger.warning("Device auth requested but no identity available")
        return params

    async def _request(
        self,
        method: str,
        params: dict[str, Any],
        request_id: str | None = None
    ) -> dict[str, Any]:
        req_id = request_id or self._make_id("req")
        await self._send_json({
            "type": "req",
            "id": req_id,
            "method": method,
            "params": params
        })

        while True:
            frame = await self._recv_frame()
            if frame.get("type") == "res" and frame.get("id") == req_id:
                if frame.get("ok") is True:
                    return frame.get("payload") or {}
                error_info = frame.get("error", {})
                code = error_info.get("code", "unknown")
                msg = error_info.get("message", "request failed")
                raise RuntimeError(f"Gateway request {method} failed: {code} - {msg}")
            self._pending_frames.append(frame)

    async def _send_json(self, payload: dict[str, Any]) -> None:
        if self._websocket is None:
            raise RuntimeError("OpenClaw gateway is not connected")
        payload_str = json.dumps(payload, separators=(",", ":"))
        await self._websocket.send(payload_str)

    async def _recv_frame(self) -> dict[str, Any]:
        if self._pending_frames:
            return self._pending_frames.pop(0)
        if self._websocket is None:
            raise RuntimeError("OpenClaw gateway is not connected")

        raw = await self._websocket.recv()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    def _event_state(self, payload: dict[str, Any]) -> str | None:
        for value in (payload, payload.get("data"), payload.get("message")):
            if isinstance(value, dict) and isinstance(value.get("state"), str):
                return value["state"]
        return None

    def _extract_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value

        if isinstance(value, list):
            return "".join(self._extract_text(item) for item in value)

        if not isinstance(value, dict):
            return ""

        content = value.get("content")
        if isinstance(content, str | list):
            return self._extract_text(content)

        for key in ("text", "delta", "chunk", "data", "message"):
            text = self._extract_text(value.get(key))
            if text:
                return text

        return ""

    def _make_id(self, prefix: str) -> str:
        import uuid
        return f"{prefix}-{uuid.uuid4().hex}"
