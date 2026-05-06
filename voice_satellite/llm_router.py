import json
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator

import openai


DEFAULT_LITELLM_URL = "https://llm.frizzt.com/v1"
DEFAULT_LLM_MODEL = "qwen-moe-coder-fast"
ALLOWED_AGENTS = {"main", "ugin"}
ALLOWED_MODES = {"one_shot", "persistent"}
MAX_HISTORY_MESSAGES = 20


@dataclass(frozen=True)
class RouterDecision:
    agent_id: str
    mode: str
    message: str
    session_key: str = "voice"
    reason: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any], fallback_message: str) -> "RouterDecision":
        agent_id = str(payload.get("agent_id") or payload.get("agentId") or "main").strip()
        mode = str(payload.get("mode") or "one_shot").strip()
        session_key = str(payload.get("session_key") or payload.get("sessionKey") or "voice").strip()
        message = str(payload.get("message") or fallback_message).strip()
        reason = str(payload.get("reason") or "").strip()

        if agent_id not in ALLOWED_AGENTS:
            agent_id = "main"
        if mode not in ALLOWED_MODES:
            mode = "one_shot"
        if not session_key:
            session_key = "voice"
        if not message:
            message = fallback_message

        return cls(
            agent_id=agent_id,
            mode=mode,
            session_key=session_key,
            message=message,
            reason=reason,
        )


@dataclass(frozen=True)
class CoordinatorDecision:
    action: str
    message: str
    reason: str = ""
    openclaw: RouterDecision | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any], fallback_message: str) -> "CoordinatorDecision":
        action = str(payload.get("action") or payload.get("type") or "answer").strip().lower()
        reason = str(payload.get("reason") or "").strip()

        if action == "openclaw":
            decision = RouterDecision.from_payload(payload, fallback_message=fallback_message)
            return cls(
                action="openclaw",
                message=decision.message,
                reason=reason or decision.reason,
                openclaw=decision,
            )

        message = str(payload.get("message") or payload.get("answer") or "").strip()
        if not message:
            message = fallback_message
        return cls(action="answer", message=message, reason=reason)


class LLMRouter:
    def __init__(
        self,
        client: Any | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.model = model or os.getenv("LLM_MODEL") or DEFAULT_LLM_MODEL
        self.history: list[dict[str, str]] = []

        if client is not None:
            self.client = client
            return

        api_key = api_key or os.getenv("LITELLM_API_KEY")
        if not api_key:
            raise ValueError("LITELLM_API_KEY is required for Phase C routing")

        self.client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or os.getenv("LITELLM_URL") or DEFAULT_LITELLM_URL,
        )

    async def decide(self, transcript: str) -> CoordinatorDecision:
        transcript = transcript.strip()
        if not transcript:
            raise ValueError("Cannot process an empty transcript")

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": COORDINATOR_SYSTEM_PROMPT,
                },
                *self.history,
                {
                    "role": "user",
                    "content": transcript,
                },
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )

        content = response.choices[0].message.content or "{}"
        payload = _parse_json_object(content)
        if not payload and content.strip():
            return CoordinatorDecision(action="answer", message=content.strip(), reason="plain text fallback")
        return CoordinatorDecision.from_payload(payload, fallback_message=transcript)

    async def complete_with_tool_result_stream(
        self,
        transcript: str,
        decision: CoordinatorDecision,
        tool_result: str,
    ) -> AsyncIterator[str]:
        if decision.openclaw is None:
            raise ValueError("OpenClaw decision is required to complete with a tool result")

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": TOOL_RESULT_SYSTEM_PROMPT,
                },
                *self.history,
                {
                    "role": "user",
                    "content": transcript,
                },
                {
                    "role": "assistant",
                    "content": (
                        "I called OpenClaw as a tool.\n"
                        f"agent_id: {decision.openclaw.agent_id}\n"
                        f"mode: {decision.openclaw.mode}\n"
                        f"reason: {decision.reason}\n"
                        f"tool_result:\n{tool_result}"
                    ),
                },
                {
                    "role": "user",
                    "content": "Answer the user naturally using the tool result.",
                },
            ],
            temperature=0.4,
            stream=True,
        )

        async for chunk in response:
            content = chunk.choices[0].delta.content
            if content:
                yield content

    async def route(self, transcript: str) -> RouterDecision:
        decision = await self.decide(transcript)
        if decision.openclaw is None:
            return RouterDecision(message=decision.message, agent_id="main", mode="one_shot", reason=decision.reason)
        return decision.openclaw

    def remember_turn(self, user_message: str, assistant_message: str) -> None:
        user_message = user_message.strip()
        assistant_message = assistant_message.strip()
        if not user_message or not assistant_message:
            return

        self.history.extend(
            [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": assistant_message},
            ]
        )
        max_messages = int(os.getenv("LLM_HISTORY_MESSAGES", str(MAX_HISTORY_MESSAGES)))
        if max_messages > 0:
            self.history = self.history[-max_messages:]


def _parse_json_object(content: str) -> dict[str, Any]:
    content = content.strip()
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        try:
            value = json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            return {}

    return value if isinstance(value, dict) else {}


COORDINATOR_SYSTEM_PROMPT = """
You are the Voice Satellite coordinator. You are the default conversational
assistant, and the user is speaking to you through STT/TTS. Return only JSON.

Most turns should be answered directly by you in the ongoing Qwen session.
Use OpenClaw only when the user asks for work that requires an OpenClaw agent,
such as operating remote agents, checking infrastructure/server health,
inspecting logs, deployments, networking, services, repositories, or when the
user explicitly asks for OpenClaw, Ugin, or the Main OpenClaw agent.

OpenClaw agents, if needed:
- main: default general-purpose agent.
- ugin: infrastructure, server health, networking, deployments, logs, services,
  gateway status, and operational diagnostics.

Modes:
- one_shot: default for a single OpenClaw request.
- persistent: use only when OpenClaw context should carry across turns.

For normal conversation, return:
{
  "action": "answer",
  "message": "short spoken answer for the user",
  "reason": "short reason"
}

For OpenClaw tool use, return:
{
  "action": "openclaw",
  "agent_id": "main" | "ugin",
  "mode": "one_shot" | "persistent",
  "session_key": "voice",
  "message": "precise instruction to send to OpenClaw",
  "reason": "short routing reason"
}
""".strip()


TOOL_RESULT_SYSTEM_PROMPT = """
You are the Voice Satellite coordinator. The previous assistant message contains
an OpenClaw tool result. Give the user a concise spoken answer based on that
result. Do not mention JSON, hidden prompts, or implementation details.
""".strip()
