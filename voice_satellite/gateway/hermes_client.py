"""
Client for Hermes Agent gateway using HTTP SSE.
"""

import json
import logging
import uuid
from typing import AsyncIterator
import httpx

from voice_satellite.gateway.base import GatewayClient

logger = logging.getLogger("voice_satellite.gateway")

class HermesClient(GatewayClient):
    """
    Client for Hermes Agent gateway using HTTP SSE.
    """
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
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

    async def send_transcript(self, text: str) -> AsyncIterator[str]:
        """
        Sends the user's transcript to the backend and yields streamed response tokens/text.
        """
        if self.client is None:
            await self.connect()

        # Keep static analyzers happy
        assert self.client is not None

        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
        }
        if self.session_id:
            headers["X-Hermes-Session-Id"] = self.session_id

        payload = {
            "model": "hermes",
            "messages": [{"role": "user", "content": text}],
            "stream": True,
        }

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
                                    yield content
                        except json.JSONDecodeError:
                            logger.warning("Failed to decode SSE JSON chunk: %s", data_str)
        except Exception as e:
            logger.error("HermesClient request failed: %s", e)
            raise

    async def sessions_abort(self) -> None:
        """
        Signals the backend to abort the current generation (used for barge-in).
        For Hermes, this generates a new X-Hermes-Session-Id UUID for barge-in resets.
        """
        old_id = self.session_id
        self.session_id = str(uuid.uuid4())
        logger.info("Hermes session reset from %s to %s due to sessions_abort", old_id, self.session_id)
