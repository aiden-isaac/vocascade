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


async def test_one_shot() -> None:
    websocket = FakeWebSocket(
        [
            {"type": "hello-ok"},
            {"type": "res", "id": "voice-test", "ok": True, "payload": {"status": "started"}},
            {
                "type": "event",
                "event": "agent",
                "payload": {"runId": "voice-test", "state": "delta", "data": {"text": "hel"}},
            },
            {
                "type": "event",
                "event": "agent",
                "payload": {"runId": "voice-test", "state": "final", "data": {"text": "hello"}},
            },
        ]
    )
    client = OpenClawGatewayClient(
        token="test-token",
        connect_impl=lambda url: fake_connect(websocket, url),
    )
    client._make_id = lambda prefix: "voice-test"

    await client.connect()
    text = await client.one_shot("ugin", "hello?")
    await client.close()

    assert text == "hello"
    assert websocket.closed is True
    assert websocket.sent[0]["auth"] == {"token": "test-token"}
    assert websocket.sent[1] == {
        "type": "req",
        "id": "voice-test",
        "method": "agent",
        "params": {
            "message": "hello?",
            "agentId": "ugin",
            "deliver": False,
            "idempotencyKey": "voice-test",
        },
    }


async def test_persistent_send() -> None:
    websocket = FakeWebSocket(
        [
            {"type": "hello-ok"},
            {"type": "res", "id": "create-test", "ok": True, "payload": {}},
            {"type": "res", "id": "voice-test", "ok": True, "payload": {"runId": "voice-test"}},
            {
                "type": "event",
                "event": "chat",
                "payload": {
                    "runId": "voice-test",
                    "sessionKey": "agent:ugin:voice",
                    "state": "delta",
                    "message": {"content": [{"type": "text", "text": "hi "}]},
                },
            },
            {
                "type": "event",
                "event": "chat",
                "payload": {
                    "runId": "voice-test",
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
    ids = {"create-session": "create-test", "voice": "voice-test"}
    client._make_id = lambda prefix: ids[prefix]

    await client.connect()
    text = await client.persistent_send("ugin", "voice", "status?")

    assert text == "hi there"
    assert websocket.sent[1] == {
        "type": "req",
        "id": "create-test",
        "method": "sessions.create",
        "params": {"agentId": "ugin", "key": "voice", "label": "Voice Satellite"},
    }
    assert websocket.sent[2] == {
        "type": "req",
        "id": "voice-test",
        "method": "chat.send",
        "params": {
            "sessionKey": "agent:ugin:voice",
            "message": "status?",
            "deliver": False,
            "idempotencyKey": "voice-test",
        },
    }


async def test_error_frame() -> None:
    websocket = FakeWebSocket(
        [
            {"type": "hello-ok"},
            {
                "type": "res",
                "id": "voice-test",
                "ok": False,
                "error": {"code": "invalid_request", "message": "bad", "retryable": False},
            },
        ]
    )
    client = OpenClawGatewayClient(
        token="test-token",
        connect_impl=lambda url: fake_connect(websocket, url),
    )
    client._make_id = lambda prefix: "voice-test"

    await client.connect()
    try:
        await client.one_shot("main", "hello")
    except GatewayError as error:
        assert error.code == "invalid_request"
        assert error.message == "bad"
        assert error.retryable is False
    else:
        raise AssertionError("GatewayError was not raised")


async def main() -> None:
    await test_one_shot()
    await test_persistent_send()
    await test_error_frame()
    print("openclaw gateway tests passed")


if __name__ == "__main__":
    asyncio.run(main())
