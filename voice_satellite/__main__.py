"""
CLI Entry point and bootstrap launcher for the voice satellite.
"""

import asyncio
import logging
import sys
import uvicorn

from voice_satellite.config import load_config
from voice_satellite.stt.whisper_stt import WhisperSTT
from voice_satellite.tts.genie_client import GenieTTSClient
from voice_satellite.audio.filler_engine import FillerEngine
from voice_satellite.gateway.openclaw_client import OpenClawClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("voice_satellite.main")

async def bootstrap():
    # 1. Load config
    config = load_config()
    
    stt_status = "unknown"
    tts_status = "unknown"
    gateway_status = "unknown"
    fillers_status = "unknown"
    
    # 2. STT init
    try:
        WhisperSTT(model_name=config.whisper_model, language=config.whisper_language)
        stt_status = f"{config.whisper_model} (CPU) ✓"
    except Exception as e:
        logger.error(f"STT initialization failed: {e}")
        stt_status = "failed ✗"
        sys.exit(1)
        
    # 3. TTS ping
    if config.skip_genie_init:
        logger.warning("Skipping Genie TTS initialization (skip flag set).")
        tts_status = f"{config.tts_character_name} (skipped) ⚠"
    elif not config.tts_onnx_model_dir or not config.tts_reference_audio or not config.tts_reference_text:
        logger.warning("TTS config incomplete. Running in degraded mode.")
        tts_status = f"{config.tts_character_name} (degraded) ⚠"
    else:
        tts_client = GenieTTSClient(tts_url=config.tts_url, character_name=config.tts_character_name)
        try:
            success = await tts_client.ping_and_load()
            if success:
                tts_status = f"{config.tts_character_name} @ {config.tts_url} ✓"
            else:
                tts_status = f"{config.tts_character_name} (unreachable) ⚠"
        except Exception as e:
            logger.warning(f"Genie TTS ping failed: {e}")
            tts_status = f"{config.tts_character_name} (failed) ⚠"
            
    # 4. Fillers load
    filler_engine = FillerEngine(filler_dir=config.filler_dir)
    try:
        count = filler_engine.load_fillers()
        fillers_status = f"{count} loaded ✓"
    except Exception as e:
        logger.warning(f"Failed to load fillers: {e}")
        fillers_status = "0 loaded ⚠"
        
    # 5. Gateway connectivity test
    gateway_client = OpenClawClient(gateway_url=config.gateway_url, gateway_token=config.gateway_token)
    try:
        gw_success = await gateway_client.test_connectivity()
        if gw_success:
            gateway_status = f"{config.gateway_url} ✓"
        else:
            gateway_status = f"{config.gateway_url} (unreachable) ⚠"
    except Exception as e:
        logger.warning(f"Gateway connectivity test failed: {e}")
        gateway_status = f"{config.gateway_url} (failed) ⚠"
        
    # Print health report
    listening_url = f"http://{config.host}:{config.port}"
    report = f"""
╔══════════════════════════════════════════════════╗
║  Voice Satellite — Startup Health Report         ║
╠══════════════════════════════════════════════════╣
║  Config:     .env loaded ✓                       ║
║  STT:        {stt_status:<36}║
║  TTS:        {tts_status:<36}║
║  Gateway:    {gateway_status:<36}║
║  Fillers:    {fillers_status:<36}║
║  Wakeword:   model.onnx (Hey Ordis) [frontend]   ║
║  Listening:  {listening_url:<36}║
╚══════════════════════════════════════════════════╝
"""
    print(report)
    return config

def main():
    config = asyncio.run(bootstrap())
    uvicorn.run("voice_satellite.server:app", host=config.host, port=config.port, log_level="info")

if __name__ == "__main__":
    main()
