"""
CLI Entry point and bootstrap launcher for the voice satellite.
"""

import asyncio
import logging
import sys
import uvicorn

from pathlib import Path

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
        tts_client = GenieTTSClient(
            tts_url=config.tts_url,
            character_name=config.tts_character_name,
            onnx_model_dir=config.tts_onnx_model_dir,
            reference_audio=config.tts_reference_audio,
            reference_text=config.tts_reference_text,
            language=config.tts_language,
        )
        try:
            await tts_client.load_character()
            if not tts_client.degraded_mode:
                tts_status = f"{config.tts_character_name} @ {config.tts_url} ✓"
            else:
                tts_status = f"{config.tts_character_name} (degraded) ⚠"
        except Exception as e:
            logger.warning(f"Genie TTS initialization failed: {e}")
            tts_status = f"{config.tts_character_name} (failed) ⚠"

            
    # 4. Fillers load
    filler_engine = FillerEngine(filler_dir=config.filler_dir)
    try:
        count = filler_engine.load_fillers()
        cats = filler_engine.get_categories()
        cat_str = ", ".join(f"{k}: {v}" for k, v in cats.items())
        fillers_status = f"{count} loaded ({cat_str}) ✓"
    except Exception as e:
        logger.warning(f"Failed to load fillers: {e}")
        fillers_status = "0 loaded ⚠"
        
    # 5. Gateway connectivity test
    gateway_client = OpenClawClient(gateway_url=config.gateway_url, gateway_token=config.gateway_token)
    try:
        gw_success = await gateway_client.test_connectivity()
        if gw_success:
            proto_str = f" (v{gateway_client.protocol})" if getattr(gateway_client, "protocol", None) is not None else ""
            gateway_status = f"{config.gateway_url}{proto_str} ✓"
        else:
            gateway_status = f"{config.gateway_url} (unreachable) ⚠"
    except Exception as e:
        logger.warning(f"Gateway connectivity test failed: {e}")
        gateway_status = f"{config.gateway_url} (failed) ⚠"

    # 6. Config and Wakeword status
    config_status = ".env loaded ✓" if Path(".env").exists() else ".env not found ⚠"
    
    wakeword_status = "None (always active)"
    try:
        import json
        model_json_path = Path("static/wakeword/model.json")
        if model_json_path.exists():
            data = json.loads(model_json_path.read_text())
            wakeword_name = data.get("name", "Unknown")
            wakeword_file = data.get("file", "model.onnx")
            wakeword_status = f"{wakeword_file} ({wakeword_name})"
    except Exception as e:
        logger.warning(f"Failed to read wakeword model config: {e}")
        
    # Print health report
    listening_url = f"http://{config.host}:{config.port}"
    report_lines = [
        ("Config", config_status),
        ("STT", stt_status),
        ("TTS", tts_status),
        ("Gateway", gateway_status),
        ("Fillers", fillers_status),
        ("Wakeword", wakeword_status),
        ("Listening", listening_url),
    ]
    header_text = "  Voice Satellite — Startup Health Report"
    formatted_lines = [f"  {name + ':':<12} {value}" for name, value in report_lines]
    max_len = max(len(header_text), max(len(line) for line in formatted_lines)) + 4

    top = "╔" + "═" * max_len + "╗"
    header = f"║{header_text:<{max_len}}║"
    divider = "╠" + "═" * max_len + "╣"
    body = "\n".join(f"║{line:<{max_len}}║" for line in formatted_lines)
    bottom = "╚" + "═" * max_len + "╝"

    report = f"\n{top}\n{header}\n{divider}\n{body}\n{bottom}\n"
    print(report)
    return config

def main():
    config = asyncio.run(bootstrap())
    uvicorn.run("voice_satellite.server:app", host=config.host, port=config.port, log_level="info")

if __name__ == "__main__":
    main()
