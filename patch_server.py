import re

with open('/home/aiden/voice-satellite/server.py', 'r') as f:
    content = f.read()

# Fix genie_tts_complete logging
old_genie = '''async def genie_tts_complete(text: str) -> bytes:
    """Calls the local genie-tts GPT-SoVITS API and returns the full WAV file as bytes."""
    logger.info(f"Generating TTS for: {text}")
    url = "http://127.0.0.1:9880/"
    params = {"text": text, "text_language": "en"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    logger.error(f"TTS API error: {response.status}")
                    return b''
                return await response.read()
    except Exception as e:
        logger.error(f"Failed to connect to TTS API: {e}")
        return b'''''

new_genie = '''async def genie_tts_complete(text: str) -> bytes:
    """Calls the local genie-tts GPT-SoVITS API and returns the full WAV file as bytes."""
    logger.info(f"Generating TTS for: {text}")
    url = "http://127.0.0.1:9880/"
    params = {"text": text, "text_language": "en"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    logger.error(f"TTS API error: {response.status} - {await response.text()}")
                    return b''
                wav_bytes = await response.read()
                if not wav_bytes:
                    logger.error("TTS API returned empty bytes.")
                    return b''
                logger.info(f"TTS API returned {len(wav_bytes)} bytes for text: {text[:20]}...")
                return wav_bytes
    except Exception as e:
        logger.error(f"Failed to connect to TTS API: {e}")
        return b'''''

content = content.replace(old_genie, new_genie)

# Update log in _run_tts
old_run_tts = '''    async def _run_tts(self, websocket: WebSocket, text: str, state: dict):
        try:
            wav_bytes = await genie_tts_complete(text)
            if not wav_bytes:
                return
            
            # Send the full WAV as base64 in one shot
            b64_data = base64.b64encode(wav_bytes).decode('utf-8')
            await websocket.send_json({
                "type": "audio",
                "data": b64_data
            })'''

new_run_tts = '''    async def _run_tts(self, websocket: WebSocket, text: str, state: dict):
        try:
            wav_bytes = await genie_tts_complete(text)
            if not wav_bytes:
                logger.error(f"_run_tts: No wav_bytes returned for {text[:20]}...")
                return
            
            # Send the full WAV as base64 in one shot
            b64_data = base64.b64encode(wav_bytes).decode('utf-8')
            logger.info(f"_run_tts: Sending base64 audio data length: {len(b64_data)}")
            await websocket.send_json({
                "type": "audio",
                "data": b64_data
            })'''

content = content.replace(old_run_tts, new_run_tts)

with open('/home/aiden/voice-satellite/server.py', 'w') as f:
    f.write(content)

print('Patched server.py successfully')
