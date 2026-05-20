import asyncio
import json
import os
import tempfile
import unittest
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

if __name__ == "__main__":
    unittest.main()
