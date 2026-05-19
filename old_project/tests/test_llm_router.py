import asyncio
import json
from types import SimpleNamespace

from voice_satellite.llm_router import CoordinatorDecision, LLMRouter, RouterDecision


class FakeCompletions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class FakeClient:
    def __init__(self, content: str) -> None:
        self.completions = FakeCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)


async def test_direct_coordinator_answer() -> None:
    payload = {
        "action": "answer",
        "message": "I can help with that.",
        "reason": "normal conversation",
    }
    client = FakeClient(json.dumps(payload))
    decision = await LLMRouter(client=client, model="test-model").decide("hello")

    assert decision == CoordinatorDecision(
        action="answer",
        message="I can help with that.",
        reason="normal conversation",
    )
    call = client.completions.calls[0]
    assert call["model"] == "test-model"
    assert call["response_format"] == {"type": "json_object"}


async def test_openclaw_tool_decision() -> None:
    payload = {
        "action": "openclaw",
        "agent_id": "ugin",
        "mode": "persistent",
        "session_key": "voice",
        "message": "Check server health.",
        "reason": "infra request",
    }
    client = FakeClient(json.dumps(payload))
    decision = await LLMRouter(client=client, model="test-model").decide("server status?")

    assert decision.action == "openclaw"
    assert decision.openclaw == RouterDecision(
        agent_id="ugin",
        mode="persistent",
        session_key="voice",
        message="Check server health.",
        reason="infra request",
    )
    call = client.completions.calls[0]
    assert call["model"] == "test-model"
    assert call["response_format"] == {"type": "json_object"}


async def test_invalid_values_are_sanitized() -> None:
    client = FakeClient(
        json.dumps(
            {
                "action": "openclaw",
                "agent_id": "unknown",
                "mode": "forever",
                "session_key": "",
                "message": "",
            }
        )
    )
    decision = await LLMRouter(client=client).decide("hello main")

    assert decision.openclaw is not None
    assert decision.openclaw.agent_id == "main"
    assert decision.openclaw.mode == "one_shot"
    assert decision.openclaw.session_key == "voice"
    assert decision.openclaw.message == "hello main"


async def test_history_is_remembered() -> None:
    router = LLMRouter(client=FakeClient(json.dumps({"action": "answer", "message": "hi"})))
    router.remember_turn("hello", "hi")
    await router.decide("what did I say?")

    messages = router.client.completions.calls[0]["messages"]
    assert {"role": "user", "content": "hello"} in messages
    assert {"role": "assistant", "content": "hi"} in messages


async def main() -> None:
    await test_direct_coordinator_answer()
    await test_openclaw_tool_decision()
    await test_invalid_values_are_sanitized()
    await test_history_is_remembered()
    print("llm router tests passed")


if __name__ == "__main__":
    asyncio.run(main())
