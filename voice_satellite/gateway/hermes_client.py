"""
Client for Hermes Agent gateway using HTTP SSE.
"""

import json
import logging
import uuid
from typing import AsyncIterator
import httpx

from voice_satellite.gateway.base import GatewayClient
from voice_satellite.telemetry import LatencyTracker

logger = logging.getLogger("voice_satellite.gateway")

class HermesClient(GatewayClient):
    """
    Client for Hermes Agent gateway using HTTP SSE.
    """
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        session_key: str | None = None,
    ) -> None:
        # Contract (005): the base URL carries /v1, e.g. http://jarlaxle:8642/v1
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/v1"):
            logger.warning(
                "HERMES_BASE_URL %r does not end with /v1 — appending it "
                "(the Hermes api_server serves chat/completions under /v1)",
                base_url,
            )
            self.base_url = f"{self.base_url}/v1"
        self.api_key = api_key
        self.session_key = session_key
        self.session_id: str | None = None
        self.client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        """
        Establishes connection or initializes resources.
        """
        if self.client is None:
            self.client = httpx.AsyncClient()
        if not self.session_id:
            self.session_id = str(uuid.uuid4())
        logger.info("HermesClient connected. Session ID: %s", self.session_id)

    async def close(self) -> None:
        """
        Closes connection and cleans up resources.
        """
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    async def send_transcript(self, text: str, *, session: str = "") -> AsyncIterator[str]:
        """
        Sends the user's transcript to the backend and yields streamed response tokens/text.
        """
        if self.client is None:
            await self.connect()

        # Keep static analyzers happy
        assert self.client is not None

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
        }
        if self.session_id:
            headers["X-Hermes-Session-Id"] = self.session_id
        if self.session_key:
            # Stable long-term-memory scope; requires API-key auth server-side.
            headers["X-Hermes-Session-Key"] = self.session_key
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": "hermes",
            "messages": [{"role": "user", "content": text}],
            "stream": True,
        }

        tracker = LatencyTracker("llm_first_token", session)
        tracker.start()
        _first_token_recorded = False

        try:
            async with self.client.stream("POST", url, headers=headers, json=payload, timeout=60.0) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        data_str = line.removeprefix("data:").strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            choices = data.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    if not _first_token_recorded:
                                        tracker.record()
                                        _first_token_recorded = True
                                    yield content
                        except json.JSONDecodeError:
                            logger.warning("Failed to decode SSE JSON chunk: %s", data_str)
        except Exception as e:
            logger.error("HermesClient request failed: %s", e)
            raise

    async def sessions_abort(self) -> None:
        """
        Signals the backend to abort the current generation (used for barge-in).
        For Hermes, this preserves the X-Hermes-Session-Id and avoids session rotation.
        """
        logger.info("Hermes sessions_abort called. Session ID %s preserved.", self.session_id)
