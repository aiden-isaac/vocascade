import asyncio
import base64
import hashlib
import json
import os
import time
import uuid
import logging
from collections.abc import Awaitable, Callable
from typing import Any, AsyncIterator

import websockets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

logger = logging.getLogger(__name__)


LAN_GATEWAY_URL = "ws://192.168.8.104:18789"
TUNNEL_GATEWAY_URL = "ws://127.0.0.1:18789"
PROTOCOL_VERSION = 3


class GatewayError(Exception):
    def __init__(self, code: str, message: str, retryable: bool) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(f"{code}: {message} (retryable={retryable})")


class OpenClawGatewayClient:
    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        request_timeout: float = 30.0,
        handshake_timeout: float = 10.0,
        connect_impl: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self.url = url or os.getenv("OPENCLAW_GATEWAY_URL") or LAN_GATEWAY_URL
        self.token = token or os.getenv("OPENCLAW_GATEWAY_TOKEN")
        self.request_timeout = request_timeout
        self.handshake_timeout = handshake_timeout
        self._connect_impl = connect_impl or websockets.connect
        self._websocket: Any | None = None
        self._pending_frames: list[dict[str, Any]] = []

        if not self.token:
            raise ValueError("OPENCLAW_GATEWAY_TOKEN is required")

    async def __aenter__(self) -> "OpenClawGatewayClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def connect(self) -> None:
        if self._websocket is not None:
            return

        try:
            logger.info("OpenClawGatewayClient.connect: Attempting connection to %s", self.url)
            self._websocket = await self._connect_impl(self.url)
            logger.info("OpenClawGatewayClient.connect: Successfully connected to %s", self.url)
        except OSError as e:
            if self.url != LAN_GATEWAY_URL:
                logger.error("OpenClawGatewayClient.connect: Connection to %s failed: %s", self.url, e)
                raise
            logger.warning("OpenClawGatewayClient.connect: LAN connection failed, falling back to tunnel: %s", e)
            self.url = TUNNEL_GATEWAY_URL
            self._websocket = await self._connect_impl(self.url)
            logger.info("OpenClawGatewayClient.connect: Successfully connected to %s", self.url)

        # OpenClaw sends a connect.challenge event BEFORE accepting our connect request.
        # We must receive it, extract the nonce, then build a signed connect request.
        logger.info("OpenClawGatewayClient.connect: Waiting for connect.challenge...")
        challenge_frame = await asyncio.wait_for(
            self._recv_frame(ignore_pending=False),
            timeout=self.handshake_timeout,
        )
        if (
            challenge_frame.get("type") == "event"
            and challenge_frame.get("event") == "connect.challenge"
        ):
            nonce = challenge_frame["payload"]["nonce"]
            logger.info("OpenClawGatewayClient.connect: Received challenge, building signed request...")
        else:
            logger.warning("Unexpected frame during handshake: %s", challenge_frame)
            nonce = None

        logger.info("OpenClawGatewayClient.connect: Sending connect request...")
        params = self._connect_params(nonce=nonce)
        payload = await self._request(
            "connect", params, request_id=self._make_id("req")
        )
        logger.debug("OpenClawGatewayClient.connect: Handshake response payload: %s", payload)

    async def close(self) -> None:
        if self._websocket is None:
            return
        await self._websocket.close()
        self._websocket = None

    async def one_shot(self, agent_id: str, message: str) -> str:
        run_id = self._make_id("voice")
        logger.info("OpenClawGatewayClient.one_shot: Sending agent request (id: %s)", run_id)
        await self._send_json(
            {"type": "req", "id": run_id, "method": "agent", "params": {
                "message": message,
                "agentId": agent_id,
                "deliver": False,
                "idempotencyKey": run_id,
            }}
        )
        
        # Phase 1: Wait for ack res
        while True:
            try:
                frame = await asyncio.wait_for(self._recv_frame(ignore_pending=True), timeout=self.request_timeout)
            except TimeoutError as e:
                logger.error("OpenClawGatewayClient.one_shot: Timeout waiting for ack")
                raise GatewayError("timeout", "Agent request timed out waiting for ack", True) from e
            
            logger.debug("OpenClawGatewayClient.one_shot: Received frame: %s", frame)
            if frame.get("type") == "res" and frame.get("id") == run_id:
                if frame.get("ok") is False:
                    raise self._gateway_error(frame)
                break
            self._pending_frames.append(frame)
        
        # Phase 2: Collect streaming event frames
        return await self._collect_final_text("agent", run_id=run_id)

    async def stream_one_shot(self, agent_id: str, message: str) -> AsyncIterator[str]:
        run_id = self._make_id("voice")
        logger.info("OpenClawGatewayClient.stream_one_shot: Sending agent request (id: %s)", run_id)
        await self._send_json(
            {"type": "req", "id": run_id, "method": "agent", "params": {
                "message": message,
                "agentId": agent_id,
                "deliver": False,
                "idempotencyKey": run_id,
            }}
        )
        
        # Phase 1: Wait for ack res
        while True:
            try:
                frame = await asyncio.wait_for(self._recv_frame(ignore_pending=True), timeout=self.request_timeout)
            except TimeoutError as e:
                logger.error("OpenClawGatewayClient.stream_one_shot: Timeout waiting for ack")
                raise GatewayError("timeout", "Agent request timed out waiting for ack", True) from e
            
            logger.debug("OpenClawGatewayClient.stream_one_shot: Received frame: %s", frame)
            if frame.get("type") == "res" and frame.get("id") == run_id:
                if frame.get("ok") is False:
                    raise self._gateway_error(frame)
                break
            self._pending_frames.append(frame)
        
        # Phase 2: Stream event frames
        async for chunk in self._stream_text("agent", run_id=run_id):
            yield chunk

    async def persistent_send(self, agent_id: str, session_key: str, message: str) -> str:
        key = self._session_create_key(agent_id, session_key)
        resolved_session_key = self._resolved_session_key(agent_id, session_key)
        create_request_id = self._make_id("create-session")

        try:
            await self._request(
                "sessions.create",
                {"agentId": agent_id, "key": key, "label": "Voice Satellite"},
                request_id=create_request_id,
            )
        except GatewayError as error:
            msg = f"{error.code}: {error.message}".lower()
            if "exist" not in msg and "already in use" not in msg:
                raise

        run_id = self._make_id("voice")
        logger.info("OpenClawGatewayClient.persistent_send: Sending chat.send request (id: %s)", run_id)
        await self._send_json(
            {"type": "req", "id": run_id, "method": "chat.send", "params": {
                "sessionKey": resolved_session_key,
                "message": message,
                "deliver": False,
                "idempotencyKey": run_id,
            }}
        )

        # Phase 1: Wait for ack res
        while True:
            try:
                frame = await asyncio.wait_for(self._recv_frame(ignore_pending=True), timeout=self.request_timeout)
            except TimeoutError as e:
                logger.error("OpenClawGatewayClient.persistent_send: Timeout waiting for ack")
                raise GatewayError("timeout", "Chat send timed out waiting for ack", True) from e

            logger.debug("OpenClawGatewayClient.persistent_send: Received frame: %s", frame)
            if frame.get("type") == "res" and frame.get("id") == run_id:
                if frame.get("ok") is False:
                    raise self._gateway_error(frame)
                break
            self._pending_frames.append(frame)

        # Phase 2: Collect streaming event frames
        return await self._collect_final_text(
            "chat",
            run_id=run_id,
            session_key=resolved_session_key,
        )

    async def stream_persistent_send(
        self,
        agent_id: str,
        session_key: str,
        message: str,
    ) -> AsyncIterator[str]:
        key = self._session_create_key(agent_id, session_key)
        resolved_session_key = self._resolved_session_key(agent_id, session_key)
        create_request_id = self._make_id("create-session")

        try:
            await self._request(
                "sessions.create",
                {"agentId": agent_id, "key": key, "label": "Voice Satellite"},
                request_id=create_request_id,
            )
        except GatewayError as error:
            msg = f"{error.code}: {error.message}".lower()
            if "exist" not in msg and "already in use" not in msg:
                raise

        run_id = self._make_id("voice")
        logger.info("OpenClawGatewayClient.stream_persistent_send: Sending chat.send request (id: %s)", run_id)
        await self._send_json(
            {"type": "req", "id": run_id, "method": "chat.send", "params": {
                "sessionKey": resolved_session_key,
                "message": message,
                "deliver": False,
                "idempotencyKey": run_id,
            }}
        )

        # Phase 1: Wait for ack res
        while True:
            try:
                frame = await asyncio.wait_for(self._recv_frame(ignore_pending=True), timeout=self.request_timeout)
            except TimeoutError as e:
                logger.error("OpenClawGatewayClient.stream_persistent_send: Timeout waiting for ack")
                raise GatewayError("timeout", "Chat send timed out waiting for ack", True) from e

            logger.debug("OpenClawGatewayClient.stream_persistent_send: Received frame: %s", frame)
            if frame.get("type") == "res" and frame.get("id") == run_id:
                if frame.get("ok") is False:
                    raise self._gateway_error(frame)
                break
            self._pending_frames.append(frame)

        # Phase 2: Stream event frames
        async for chunk in self._stream_text(
            "chat",
            run_id=run_id,
            session_key=resolved_session_key,
        ):
            yield chunk

    def _get_device_info(self) -> tuple[str | None, str | None]:
        device_json_path = os.path.join(
            os.path.expanduser("~"), ".openclaw", "identity", "device.json"
        )
        if not os.path.exists(device_json_path):
            logger.warning("No device.json found at %s", device_json_path)
            return None, None
        try:
            with open(device_json_path, "r") as f:
                data = json.load(f)
            private_key_pem = data.get("privateKeyPem")
            public_key_pem = data.get("publicKeyPem")
            stored_device_id = data.get("deviceId")
            if not private_key_pem or not public_key_pem:
                logger.warning("device.json missing key material")
                return None, None
            private_key = serialization.load_pem_private_key(
                private_key_pem.encode("utf-8"), password=None
            )
            raw_public_key_bytes = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            derived_device_id = hashlib.sha256(raw_public_key_bytes).hexdigest()
            logger.info("Derived device_id=%s (stored=%s)", derived_device_id, stored_device_id)
            return private_key, public_key_pem
        except Exception as e:
            logger.error("Failed to load device identity: %s", e)
            return None, None

    def _build_device_auth(self, nonce: str) -> dict[str, Any] | None:
        private_key, public_key_pem = self._get_device_info()
        if private_key is None:
            logger.warning("No device identity available; skipping device auth")
            return None
        client_id = "cli"
        client_mode = "cli"
        role = "operator"
        scopes_csv = ",".join(["operator.read", "operator.write"])
        signed_at = int(time.time() * 1000)
        raw_pk = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        device_id = hashlib.sha256(raw_pk).hexdigest()
        payload_str = f"v2|{device_id}|{client_id}|{client_mode}|{role}|{scopes_csv}|{signed_at}|{self.token}|{nonce}"
        logger.debug("Signing device auth payload: %s", payload_str[:80] + "...")
        signature_bytes = private_key.sign(payload_str.encode("utf-8"))
        signature_b64 = base64.b64encode(signature_bytes).decode("utf-8")
        return {
            "id": device_id,
            "publicKey": public_key_pem,
            "signature": signature_b64,
            "signedAt": signed_at,
            "nonce": nonce,
        }

    def _connect_params(self, nonce: str | None = None) -> dict[str, Any]:
        params = {
            "minProtocol": PROTOCOL_VERSION,
            "maxProtocol": PROTOCOL_VERSION,
            "client": {
                "id": "cli",
                "mode": "cli",
                "version": "0.1.0",
                "platform": "linux",
            },
            "role": "operator",
            "scopes": ["operator.read", "operator.write"],
            "auth": {"token": self.token},
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
        request_id: str | None = None,
    ) -> dict[str, Any]:
        request_id = request_id or self._make_id("req")
        logger.info("OpenClawGatewayClient._request: %s (id: %s) params: %r", method, request_id, params)
        await self._send_json(
            {"type": "req", "id": request_id, "method": method, "params": params}
        )

        while True:
            try:
                frame = await asyncio.wait_for(self._recv_frame(ignore_pending=True), timeout=self.request_timeout)
            except TimeoutError as e:
                logger.error("OpenClawGatewayClient._request: Timeout waiting for response to %s (id: %s)", method, request_id)
                raise GatewayError("timeout", f"Request {method} timed out", True) from e

            logger.debug("OpenClawGatewayClient._request: Received frame: %s", frame)
            if frame.get("type") == "res" and frame.get("id") == request_id:
                if frame.get("ok") is True:
                    return frame.get("payload") or {}
                raise self._gateway_error(frame)
            self._pending_frames.append(frame)

    async def _stream_text(
        self,
        event_name: str,
        run_id: str | None = None,
        session_key: str | None = None,
    ) -> AsyncIterator[str]:
        yielded_any = False

        while True:
            frame = await asyncio.wait_for(self._recv_frame(), timeout=self.request_timeout)
            if frame.get("type") == "res" and frame.get("ok") is False:
                raise self._gateway_error(frame)

            if frame.get("type") != "event" or frame.get("event") != event_name:
                continue

            payload = frame.get("payload") or {}
            if not self._matches_event(payload, run_id=run_id, session_key=session_key):
                continue

            state = self._event_state(payload)
            text = self._extract_text(payload)

            if state in {"aborted", "error"}:
                raise GatewayError(state, text or f"{event_name} stream {state}", False)

            if state == "final":
                if text and not yielded_any:
                    yield text
                return

            if text:
                yielded_any = True
                yield text

    async def _collect_final_text(
        self,
        event_name: str,
        run_id: str | None = None,
        session_key: str | None = None,
    ) -> str:
        chunks: list[str] = []

        while True:
            frame = await asyncio.wait_for(self._recv_frame(), timeout=self.request_timeout)
            if frame.get("type") == "res" and frame.get("ok") is False:
                raise self._gateway_error(frame)

            if frame.get("type") != "event" or frame.get("event") != event_name:
                continue

            payload = frame.get("payload") or {}
            if not self._matches_event(payload, run_id=run_id, session_key=session_key):
                continue

            state = self._event_state(payload)
            text = self._extract_text(payload)

            if state in {"aborted", "error"}:
                raise GatewayError(state, text or f"{event_name} stream {state}", False)

            if state == "final":
                return text or "".join(chunks)

            if text:
                chunks.append(text)

    async def _send_json(self, payload: dict[str, Any]) -> None:
        if self._websocket is None:
            raise RuntimeError("OpenClaw gateway is not connected")
        payload_str = json.dumps(payload, separators=(",", ":"))
        logger.debug("OpenClawGatewayClient._send_json: %s", payload_str)
        await self._websocket.send(payload_str)

    async def _recv_frame(self, ignore_pending: bool = False) -> dict[str, Any]:
        if not ignore_pending and self._pending_frames:
            return self._pending_frames.pop(0)
        if self._websocket is None:
            raise RuntimeError("OpenClaw gateway is not connected")

        raw = await self._websocket.recv()
        logger.debug("OpenClawGatewayClient._recv_frame (raw): %r", raw)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        frame = json.loads(raw)
        if not isinstance(frame, dict):
            raise ValueError("Gateway frame must be a JSON object")
        return frame

    def _gateway_error(self, frame: dict[str, Any]) -> GatewayError:
        error = frame.get("error") or {}
        error_code = str(error.get("code") or "gateway_error")
        error_msg = str(error.get("message") or "OpenClaw gateway request failed")
        logger.error("OpenClawGatewayClient encountered error: %s - %s", error_code, error_msg)
        return GatewayError(
            error_code,
            error_msg,
            bool(error.get("retryable", False)),
        )

    def _matches_event(
        self,
        payload: dict[str, Any],
        run_id: str | None,
        session_key: str | None,
    ) -> bool:
        payload_run_id = payload.get("runId") or payload.get("id")
        payload_session_key = payload.get("sessionKey")

        if run_id and payload_run_id and payload_run_id != run_id:
            return False
        if session_key and payload_session_key and payload_session_key != session_key:
            return False
        return True

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

    def _session_create_key(self, agent_id: str, session_key: str) -> str:
        prefix = f"agent:{agent_id}:"
        if session_key.startswith(prefix):
            return session_key.removeprefix(prefix)
        return session_key

    def _resolved_session_key(self, agent_id: str, session_key: str) -> str:
        if session_key.startswith("agent:"):
            return session_key
        return f"agent:{agent_id}:{session_key}"

    def _make_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"
