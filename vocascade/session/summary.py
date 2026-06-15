"""
vocascade/session/summary.py — Session-end memory gist (US10 / FR-090, FR-091).

When some turns of a session are handled locally (smalltalk, datetime, timers),
the Hermes agent never sees them, so its long-term memory diverges from what the
user actually said. At session end this sends a concise natural-language *gist*
(not a transcript) of the session to the memory service so that context is
preserved.

Best-effort by contract (FR-091): generation and the POST are both wrapped so a
missing LLM, an empty session, or an unreachable memory service is logged and
returns falsy — it MUST NOT block session teardown. The caller fires this as a
background task so teardown never waits on the network.
"""

import logging

import httpx

logger = logging.getLogger("vocascade.session.summary")

_SUMMARY_SYSTEM = (
    "You compress a short voice-assistant session into ONE or TWO sentences of "
    "gist for the assistant's long-term memory: what the user asked about and "
    "what was done or said. Plain prose, third person, no transcript, no lists, "
    "no preamble — just the gist."
)


class SessionSummarizer:
    """Generates a session gist with the local LLM and POSTs it to the memory
    service. Enabled only when a memory URL is configured."""

    def __init__(self, memory_url: str, local_llm, *, peer: str = "", timeout: float = 5.0):
        self.memory_url = memory_url or ""
        self.local_llm = local_llm
        self.peer = peer
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.memory_url)

    async def summarize(self, turns: list) -> str | None:
        """Build a one/two-sentence gist from the session's turns. Returns None
        when there is nothing to summarize or the LLM is unavailable/failing."""
        if not turns or self.local_llm is None:
            return None
        convo = "\n".join(
            f"User: {t['user']}\nAssistant: {t['assistant']}"
            for t in turns if t.get("user") and t.get("assistant")
        )
        if not convo.strip():
            return None
        try:
            gist = await self.local_llm.chat(
                [{"role": "system", "content": _SUMMARY_SYSTEM},
                 {"role": "user", "content": convo}],
                temperature=0.3, max_tokens=80,
            )
        except Exception as e:
            logger.warning("Session summary generation failed (non-blocking): %s", e)
            return None
        gist = (gist or "").strip()
        return gist or None

    async def send(self, gist: str, *, session_id: str = "") -> bool:
        """POST the gist to the memory service. Never raises (FR-091)."""
        if not self.enabled or not gist:
            return False
        payload = {"content": gist, "session_id": session_id, "peer": self.peer}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self.memory_url, json=payload)
                resp.raise_for_status()
        except Exception as e:
            logger.warning("Session summary POST failed (non-blocking): %s", e)
            return False
        logger.info("Session summary sent to memory service (%d chars).", len(gist))
        return True

    async def summarize_and_send(self, turns: list, *, session_id: str = "") -> bool:
        """Full path: gist the turns and POST. Returns True only on a sent summary.
        Safe to fire as a background task — it never raises."""
        if not self.enabled:
            return False
        try:
            gist = await self.summarize(turns)
            if not gist:
                return False
            return await self.send(gist, session_id=session_id)
        except Exception as e:  # defensive: teardown must never see an exception
            logger.warning("Session summary failed (non-blocking): %s", e)
            return False
