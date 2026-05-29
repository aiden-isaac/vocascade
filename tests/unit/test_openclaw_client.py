import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
from voice_satellite.gateway import OpenClawClient

class FakeWebSocket:
    def __init__(self, incoming: list[dict]) -> None:
        self.incoming = [json.dumps(frame) for frame in incoming]
        self.sent: list[dict] = []
        self.closed = False

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def recv(self) -> str:
        if not self.incoming:
            raise AssertionError("FakeWebSocket has no queued frames")
        return self.incoming.pop(0)

    async def close(self) -> None:
        self.closed = True

async def fake_connect(websocket: FakeWebSocket, url: str) -> FakeWebSocket:
    return websocket

def _make_challenge(nonce: str = "test-nonce") -> dict:
    return {
        "type": "event",
        "event": "connect.challenge",
        "payload": {"nonce": nonce},
    }

def _make_hello_ok(req_id: str = "req-1") -> dict:
    return {
        "type": "res",
        "id": req_id,
        "ok": True,
        "payload": {
            "type": "hello-ok",
            "protocol": 3,
            "server": {"version": "test"},
            "auth": {"role": "operator", "scopes": ["operator.read", "operator.write"]},
        },
    }

class TestOpenClawClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.device_key_path = Path(self.temp_dir.name) / "device.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_one_shot(self):
        websocket = FakeWebSocket([
            _make_challenge(),
            _make_hello_ok("connect-id"),
            {
                "type": "res",
                "id": "agent-test",
                "ok": True,
                "payload": {"status": "started"},
            },
            {
                "type": "event",
                "event": "agent",
                "payload": {"runId": "agent-test", "state": "delta", "data": {"text": "hel"}},
            },
            {
                "type": "event",
                "event": "agent",
                "payload": {"runId": "agent-test", "state": "final", "data": {"text": "hello"}},
            },
        ])
        
        client = OpenClawClient(
            gateway_url="ws://localhost:18789",
            gateway_token="test-token",
            device_key_path=self.device_key_path,
            connect_impl=lambda url: fake_connect(websocket, url)
        )
        client._make_id = lambda prefix: {
            "req": "connect-id",
            "voice": "agent-test",
        }.get(prefix, f"{prefix}-id")

        await client.connect()
        run_id = await client.send_message("ugin", "hello?")
        self.assertEqual(run_id, "agent-test")
        
        # Stream response
        chunks = []
        async for chunk in client.stream_response():
            chunks.append(chunk)
        text = "".join(chunks)
        self.assertEqual(text, "hello")
        
        await client.close()
        self.assertTrue(websocket.closed)
        self.assertEqual(websocket.sent[0]["params"]["auth"], {"token": "test-token"})

    async def test_persistent_send(self):
        websocket = FakeWebSocket([
            _make_challenge(),
            _make_hello_ok("connect-id"),
            {
                "type": "res",
                "id": "create-id",
                "ok": True,
                "payload": {},
            },
            {
                "type": "res",
                "id": "chat-test",
                "ok": True,
                "payload": {"runId": "chat-test"},
            },
            {
                "type": "event",
                "event": "chat",
                "payload": {
                    "runId": "chat-test",
                    "sessionKey": "agent:ugin:voice",
                    "state": "delta",
                    "message": {"content": [{"type": "text", "text": "hi "}]},
                },
            },
            {
                "type": "event",
                "event": "chat",
                "payload": {
                    "runId": "chat-test",
                    "sessionKey": "agent:ugin:voice",
                    "state": "final",
                    "message": {"content": [{"type": "text", "text": "hi there"}]},
                },
            },
        ])
        
        client = OpenClawClient(
            gateway_url="ws://localhost:18789",
            gateway_token="test-token",
            device_key_path=self.device_key_path,
            connect_impl=lambda url: fake_connect(websocket, url)
        )
        client._make_id = lambda prefix: {
            "req": "connect-id",
            "create-session": "create-id",
            "voice": "chat-test",
        }.get(prefix, f"{prefix}-id")

        await client.connect()
        await client.send_message("ugin", "status?", mode="persistent", session_key="voice")
        
        chunks = []
        async for chunk in client.stream_response():
            chunks.append(chunk)
        self.assertEqual("".join(chunks), "hi there")
        await client.close()

        self.assertEqual(websocket.sent[1], {
            "type": "req",
            "id": "create-id",
            "method": "sessions.create",
            "params": {"agentId": "ugin", "key": "voice", "label": "Voice Satellite"},
        })
        self.assertEqual(websocket.sent[2], {
            "type": "req",
            "id": "chat-test",
            "method": "chat.send",
            "params": {
                "sessionKey": "agent:ugin:voice",
                "message": "status?",
                "deliver": False,
                "idempotencyKey": "chat-test",
            },
        })

    async def test_error_frame(self):
        websocket = FakeWebSocket([
            _make_challenge(),
            _make_hello_ok("connect-id"),
            {
                "type": "res",
                "id": "agent-test",
                "ok": False,
                "error": {"code": "invalid_request", "message": "bad", "retryable": False},
            },
        ])
        
        client = OpenClawClient(
            gateway_url="ws://localhost:18789",
            gateway_token="test-token",
            device_key_path=self.device_key_path,
            connect_impl=lambda url: fake_connect(websocket, url)
        )
        client._make_id = lambda prefix: {
            "req": "connect-id",
            "voice": "agent-test",
        }.get(prefix, f"{prefix}-id")

        await client.connect()
        with self.assertRaises(RuntimeError) as context:
            await client.send_message("main", "hello")
        self.assertIn("invalid_request", str(context.exception))

    async def test_sessions_abort_sends_rpc(self):
        """sessions_abort() sends a sessions.abort RPC with the correct session key."""
        abort_response = {
            "type": "res",
            "id": "abort-id",
            "ok": True,
            "payload": {},
        }
        websocket = FakeWebSocket([
            _make_challenge(),
            _make_hello_ok("connect-id"),
            abort_response,
        ])

        client = OpenClawClient(
            gateway_url="ws://localhost:18789",
            gateway_token="test-token",
            device_key_path=self.device_key_path,
            connect_impl=lambda url: fake_connect(websocket, url),
        )
        client._make_id = lambda prefix: {
            "req": "connect-id",
            "abort": "abort-id",
        }.get(prefix, f"{prefix}-id")

        await client.connect()
        # Must not raise
        await client.sessions_abort(session_key="agent:main:voice")

        abort_frame = websocket.sent[1]
        self.assertEqual(abort_frame["method"], "sessions.abort")
        self.assertEqual(abort_frame["params"]["sessionKey"], "agent:main:voice")

    async def test_sessions_abort_error_is_nonfatal(self):
        """sessions_abort() swallows errors and logs a warning — never raises."""
        websocket = FakeWebSocket([
            _make_challenge(),
            _make_hello_ok("connect-id"),
            {
                "type": "res",
                "id": "abort-id",
                "ok": False,
                "error": {"code": "not_found", "message": "session not found", "retryable": False},
            },
        ])

        client = OpenClawClient(
            gateway_url="ws://localhost:18789",
            gateway_token="test-token",
            device_key_path=self.device_key_path,
            connect_impl=lambda url: fake_connect(websocket, url),
        )
        client._make_id = lambda prefix: {
            "req": "connect-id",
            "abort": "abort-id",
        }.get(prefix, f"{prefix}-id")

        await client.connect()
        # Should NOT raise even though the gateway returned an error
        await client.sessions_abort(session_key="agent:main:voice", run_id="stale-run")

    @patch("voice_satellite.gateway.openclaw_client.LatencyTracker")
    async def test_send_transcript_latency(self, mock_tracker_cls):
        websocket = FakeWebSocket([
            _make_challenge(),
            _make_hello_ok("connect-id"),
            {
                "type": "res",
                "id": "create-id",
                "ok": True,
                "payload": {},
            },
            {
                "type": "res",
                "id": "chat-test",
                "ok": True,
                "payload": {"runId": "chat-test"},
            },
            {
                "type": "event",
                "event": "chat",
                "payload": {
                    "runId": "chat-test",
                    "sessionKey": "agent:main:default",
                    "state": "delta",
                    "message": {"content": [{"type": "text", "text": "hi "}]},
                },
            },
            {
                "type": "event",
                "event": "chat",
                "payload": {
                    "runId": "chat-test",
                    "sessionKey": "agent:main:default",
                    "state": "final",
                    "message": {"content": [{"type": "text", "text": "hi there"}]},
                },
            },
        ])
        
        client = OpenClawClient(
            gateway_url="ws://localhost:18789",
            gateway_token="test-token",
            device_key_path=self.device_key_path,
            connect_impl=lambda url: fake_connect(websocket, url)
        )
        client._make_id = lambda prefix: {
            "req": "connect-id",
            "create-session": "create-id",
            "voice": "chat-test",
        }.get(prefix, f"{prefix}-id")

        mock_tracker = MagicMock()
        mock_tracker_cls.return_value = mock_tracker

        await client.connect()
        
        tokens = []
        async for token in client.send_transcript("hello?", session="sess789"):
            tokens.append(token)
            
        mock_tracker_cls.assert_called_once_with("llm_first_token", "sess789")
        mock_tracker.start.assert_called_once()
        mock_tracker.record.assert_called_once()


if __name__ == "__main__":
    unittest.main()
