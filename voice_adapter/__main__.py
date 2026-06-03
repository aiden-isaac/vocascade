"""
Main entry point for the Pipecat Voice Adapter.
Loads config, prints a health report, and launches the FastAPI app.
"""

import sys
import logging
from voice_adapter.config import load_config

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("voice_adapter.main")

def print_health_report(config):
    """Prints a health report summarizing the loaded configuration."""
    print("=" * 60)
    print("  PIPECAT VOICE ADAPTER HEALTH REPORT")
    print("=" * 60)
    print(f"Server Host/Port:   {config.host}:{config.port}")
    print(f"Audio Sample Rates: IN: {config.audio_in_sample_rate} Hz, OUT: {config.audio_out_sample_rate} Hz")
    print(f"Hermes Gateway:     {config.hermes_base_url}")
    print(f"Hermes Model:       {config.hermes_model}")
    print(f"Hermes SSE URL:     {config.hermes_sse_url}")
    print(f"Genie TTS URL:      {config.tts_url}")
    print(f"Genie Character:    {config.tts_character_name}")
    print(f"Whisper STT:        {config.whisper_model} ({config.whisper_language})")
    print(f"Hermes Memory Path: {config.hermes_memory_path}")
    print(f"Honcho API URL:     {config.honcho_api_url} (Poll interval: {config.honcho_poll_interval}s)")
    print(f"LiteLLM Health URL: {config.litellm_health_url}")
    print(f"Offline Schedule:   {config.offline_start_hour}:00 - {config.offline_end_hour}:00")
    print(f"Offline Queue Path: {config.offline_queue_path}")
    print(f"Skip Genie Init:    {config.skip_genie_init}")
    print("=" * 60)

def main():
    try:
        config = load_config()
        print_health_report(config)
        logger.info("Configuration loaded successfully. Starting FastAPI uvicorn server...")
        
        import uvicorn
        from voice_adapter.adapter import app
        
        uvicorn.run(app, host=config.host, port=config.port)
    except Exception as e:
        logger.critical(f"Failed to start voice adapter: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()

