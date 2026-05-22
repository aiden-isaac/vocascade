"""
Base class defining the GatewayClient interface for swappable backends.
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator

class GatewayClient(ABC):
    """
    Abstract base class for Voice Satellite gateway clients (Hermes, OpenClaw).
    """

    @abstractmethod
    async def connect(self) -> None:
        """
        Establishes connection or initializes resources.
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """
        Closes connection and cleans up resources.
        """
        pass

    @abstractmethod
    async def send_transcript(self, text: str) -> AsyncIterator[str]:
        """
        Sends the user's transcript to the backend and yields streamed response tokens/text.
        """
        pass

    @abstractmethod
    async def sessions_abort(self) -> None:
        """
        Signals the backend to abort the current generation (used for barge-in).
        """
        pass
