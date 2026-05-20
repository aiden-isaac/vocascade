"""
HTTP client for the Genie TTS server.
"""

class GenieTTSClient:
    def __init__(self, tts_url: str, character_name: str):
        self.tts_url = tts_url
        self.character_name = character_name

    async def ping_and_load(self) -> bool:
        # Placeholder verification
        return True
