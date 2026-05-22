"""
Client for Hermes Agent gateway using HTTP SSE.
"""

import logging
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
        pass

    async def close(self) -> None:
        """
        Closes connection and cleans up resources.
        """
        pass

    async def send_transcript(self, text: str) -> AsyncIterator[str]:
        """
        Sends the user's transcript to the backend and yields streamed response tokens/text.
        """
        # Placeholder async generator
        if False:
            yield ""

    async def sessions_abort(self) -> None:
        """
        Signals the backend to abort the current generation (used for barge-in).
        """
        pass
