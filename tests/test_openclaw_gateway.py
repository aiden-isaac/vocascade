import asyncio
import json

from voice_satellite.openclaw_gateway import GatewayError, OpenClawGatewayClient


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
    assert url.startswith("ws://")
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


async def test_one_shot() -> None:
    # Frame sequence:
    # 0: challenge (consumed by connect)
    # 1: hello-ok for connect (consumed by _request)
    # 2: ack res for agent call
    # 3-4: agent event frames (delta, final)
    websocket = FakeWebSocket(
        [
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
        ]
    )
    client = OpenClawGatewayClient(
        token="test-token",
        connect_impl=lambda url: fake_connect(websocket, url),
    )
    client._make_id = lambda prefix: {
        "req": "connect-id",
        "voice": "agent-test",
    }.get(prefix, f"{prefix}-id")

    await client.connect()
    text = await client.one_shot("ugin", "hello?")
    await client.close()

    assert text == "hello"
    assert websocket.closed is True
    assert len(websocket.sent) == 2
    assert websocket.sent[0]["params"]["auth"] == {"token": "test-token"}
    assert websocket.sent[1] == {
        "type": "req",
        "id": "agent-test",
        "method": "agent",
        "params": {
            "message": "hello?",
            "agentId": "ugin",
            "deliver": False,
            "idempotencyKey": "agent-test",
        },
    }


async def test_persistent_send() -> None:
    # Frame sequence:
    # 0: challenge
    # 1: hello-ok for connect
    # 2: ack for sessions.create
    # 3: ack for chat.send
    # 4-5: chat event frames
    websocket = FakeWebSocket(
        [
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
        ]
    )
    client = OpenClawGatewayClient(
        token="test-token",
        connect_impl=lambda url: fake_connect(websocket, url),
    )
    client._make_id = lambda prefix: {
        "req": "connect-id",
        "create-session": "create-id",
        "voice": "chat-test",
    }.get(prefix, f"{prefix}-id")

    await client.connect()
    text = await client.persistent_send("ugin", "voice", "status?")

    assert text == "hi there"
    assert len(websocket.sent) == 3
    assert websocket.sent[1] == {
        "type": "req",
        "id": "create-id",
        "method": "sessions.create",
        "params": {"agentId": "ugin", "key": "voice", "label": "Voice Satellite"},
    }
    assert websocket.sent[2] == {
        "type": "req",
        "id": "chat-test",
        "method": "chat.send",
        "params": {
            "sessionKey": "agent:ugin:voice",
            "message": "status?",
            "deliver": False,
            "idempotencyKey": "chat-test",
        },
    }


async def test_error_frame() -> None:
    websocket = FakeWebSocket(
        [
            _make_challenge(),
            _make_hello_ok("connect-id"),
            {
                "type": "res",
                "id": "agent-test",
                "ok": False,
                "error": {"code": "invalid_request", "message": "bad", "retryable": False},
            },
        ]
    )
    client = OpenClawGatewayClient(
        token="test-token",
        connect_impl=lambda url: fake_connect(websocket, url),
    )
    client._make_id = lambda prefix: {
        "req": "connect-id",
        "voice": "agent-test",
    }.get(prefix, f"{prefix}-id")

    await client.connect()
    try:
        await client.one_shot("main", "hello")
    except GatewayError as error:
        assert error.code == "invalid_request"
        assert error.message == "bad"
        assert error.retryable is False
    else:
        raise AssertionError("GatewayError was not raised")


async def test_connect_error() -> None:
    websocket = FakeWebSocket(
        [
            _make_challenge(),
            {
                "type": "res",
                "id": "connect-id",
                "ok": False,
                "error": {"code": "AUTH_TOKEN_MISMATCH", "message": "token mismatch", "retryable": True},
            },
        ]
    )
    client = OpenClawGatewayClient(
        token="bad-token",
        connect_impl=lambda url: fake_connect(websocket, url),
    )
    client._make_id = lambda prefix: "connect-id"

    try:
        await client.connect()
    except GatewayError as error:
        assert error.code == "AUTH_TOKEN_MISMATCH"
        assert error.retryable is True
    else:
        raise AssertionError("GatewayError was not raised")


async def test_no_device_json() -> None:
    """Test that connect works even when device.json is missing (graceful degradation)."""
    import os

    original_home = os.environ.get("HOME", "/home/aiden")
    os.environ["HOME"] = "/tmp/nonexistent"

    try:
        websocket = FakeWebSocket(
            [
                _make_challenge(),
                _make_hello_ok("connect-id"),
            ]
        )
        client = OpenClawGatewayClient(
            token="test-token",
            connect_impl=lambda url: fake_connect(websocket, url),
        )
        client._make_id = lambda prefix: "connect-id"

        await client.connect()
        assert len(websocket.sent) >= 1
        assert websocket.sent[0]["params"]["auth"] == {"token": "test-token"}
    finally:
        os.environ["HOME"] = original_home


async def main() -> None:
    await test_no_device_json()
    await test_one_shot()
    await test_persistent_send()
    await test_error_frame()
    await test_connect_error()
    print("openclaw gateway tests passed")


if __name__ == "__main__":
    asyncio.run(main())
