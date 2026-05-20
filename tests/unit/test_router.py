import asyncio
import json
import unittest
import os
from types import SimpleNamespace

from voice_satellite.llm import LLMRouter, RouterDecision, CoordinatorDecision

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

class TestLLMRouter(unittest.IsolatedAsyncioTestCase):
    async def test_direct_coordinator_answer(self) -> None:
        payload = {
            "action": "answer",
            "message": "I can help with that.",
            "reason": "normal conversation",
        }
        client = FakeClient(json.dumps(payload))
        decision = await LLMRouter(client=client, model="test-model").decide("hello")

        self.assertEqual(decision, CoordinatorDecision(
            action="answer",
            message="I can help with that.",
            reason="normal conversation",
        ))
        call = client.completions.calls[0]
        self.assertEqual(call["model"], "test-model")
        self.assertEqual(call["response_format"], {"type": "json_object"})

    async def test_openclaw_tool_decision(self) -> None:
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

        self.assertEqual(decision.action, "openclaw")
        self.assertEqual(decision.openclaw, RouterDecision(
            agent_id="ugin",
            mode="persistent",
            session_key="voice",
            message="Check server health.",
            reason="infra request",
        ))
        call = client.completions.calls[0]
        self.assertEqual(call["model"], "test-model")
        self.assertEqual(call["response_format"], {"type": "json_object"})

    async def test_invalid_values_are_sanitized(self) -> None:
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

        self.assertIsNotNone(decision.openclaw)
        self.assertEqual(decision.openclaw.agent_id, "main")
        self.assertEqual(decision.openclaw.mode, "one_shot")
        self.assertEqual(decision.openclaw.session_key, "voice")
        self.assertEqual(decision.openclaw.message, "hello main")

    async def test_history_is_remembered(self) -> None:
        router = LLMRouter(client=FakeClient(json.dumps({"action": "answer", "message": "hi"})))
        router.remember_turn("hello", "hi")
        await router.decide("what did I say?")

        messages = router.client.completions.calls[0]["messages"]
        self.assertIn({"role": "user", "content": "hello"}, messages)
        self.assertIn({"role": "assistant", "content": "hi"}, messages)

    async def test_new_route_signature(self) -> None:
        payload = {
            "action": "answer",
            "message": "I remembered.",
        }
        client = FakeClient(json.dumps(payload))
        router = LLMRouter(client=client)
        
        history = [{"role": "user", "content": "prev request"}, {"role": "assistant", "content": "prev response"}]
        decision = await router.route("new request", history=history, task_summaries="1 background task running")
        
        self.assertEqual(decision.action, "answer")
        self.assertEqual(decision.message, "I remembered.")
        
        call = client.completions.calls[0]
        self.assertEqual(call["messages"][0]["role"], "system")
        self.assertIn("1 background task running", call["messages"][0]["content"])
        self.assertEqual(call["messages"][1], {"role": "user", "content": "prev request"})
        self.assertEqual(call["messages"][2], {"role": "assistant", "content": "prev response"})
        self.assertEqual(call["messages"][3], {"role": "user", "content": "new request"})

    async def test_ordis_personality_injected(self) -> None:
        os.environ["GENIE_CHARACTER_NAME"] = "ordis"
        try:
            client = FakeClient(json.dumps({"action": "answer", "message": "yes"}))
            router = LLMRouter(client=client)
            await router.decide("hello")
            
            call = client.completions.calls[0]
            system_prompt = call["messages"][0]["content"]
            self.assertIn("ROLEPLAY INSTRUCTIONS: ORDIS PERSONALITY", system_prompt)
        finally:
            del os.environ["GENIE_CHARACTER_NAME"]

if __name__ == "__main__":
    unittest.main()
