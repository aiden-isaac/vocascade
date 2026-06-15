"""
vocascade/gateway/local_llm.py — Async local LLM client.
"""

import httpx
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("vocascade.gateway.local_llm")

class LocalLLM:
    """
    Lightweight async client for calling the local LLM chat/completions endpoint.
    Used for smalltalk generation and medium-stage classification.
    """
    def __init__(self, base_url: str, api_key: Optional[str] = None, model: str = "qwen-moe-coder-fast",
                 timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        # Per-call HTTP timeout. The medium-stage classifier uses a short one so a
        # hung local LLM degrades fast instead of stalling routing (US7).
        self.timeout = timeout

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 150
    ) -> str:
        """
        Sends a chat completions request to the local LLM.
        """
        headers = {
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }

        async with httpx.AsyncClient() as client:
            try:
                url = f"{self.base_url}/chat/completions"
                logger.debug(f"Sending request to local LLM: {url} with model {self.model}")
                response = await client.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout
                )
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                logger.debug(f"Local LLM response: {content}")
                return content
            except Exception as e:
                logger.error(f"Local LLM chat request failed: {e}")
                raise
