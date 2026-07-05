"""
Main entry point for the Vocascade voice server.
Loads config, prints a health report, and launches the FastAPI app.
"""

import asyncio
import sys
import logging
from vocascade.config import load_config

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("vocascade.main")

PROBE_TIMEOUT_S = 3.0


def probe_llm(config) -> str:
    """One tiny chat call → 'OK' | 'AUTH REJECTED' | 'UNREACHABLE' verdict (D5).
    Diagnostic only — a failed probe warns but never blocks startup (the
    endpoint may legitimately come up after the server)."""
    from vocascade.gateway.local_llm import LocalLLM, LLMAuthError, LLMUnreachableError

    async def _probe():
        llm = LocalLLM(base_url=config.llm_base_url, api_key=config.llm_api_key,
                       model=config.llm_model, timeout=PROBE_TIMEOUT_S)
        try:
            await llm.chat([{"role": "user", "content": "ping"}], max_tokens=1)
            return "OK"
        except LLMAuthError:
            return "AUTH REJECTED — check LLM_API_KEY"
        except LLMUnreachableError:
            return "UNREACHABLE — check LLM_BASE_URL / that the endpoint is running"
        except Exception as e:
            return f"ERROR — {e}"

    return asyncio.run(_probe())


def probe_hermes(config) -> str:
    """Capabilities probe verdict for the configured Hermes endpoint (D5)."""
    if not config.hermes_base_url:
        return "not configured (local-only mode)"
    from vocascade.hermes_run_client import HermesRunClient
    import httpx

    async def _probe():
        client = HermesRunClient(
            base_url=config.hermes_base_url, api_key=config.hermes_api_key,
            http_client=httpx.AsyncClient(timeout=PROBE_TIMEOUT_S),
        )
        try:
            caps = await client.probe_capabilities()
            if not caps.raw:
                return "UNREACHABLE — check HERMES_BASE_URL"
            return "OK (runs API)" if caps.supports_runs else "OK (chat fallback only)"
        finally:
            await client.client.aclose()

    return asyncio.run(_probe())


def print_health_report(config):
    """Prints a health report summarizing the loaded configuration, including
    live probe verdicts for the LLM and Hermes endpoints (never blocking)."""
    llm_verdict = probe_llm(config)
    hermes_verdict = probe_hermes(config)
    print("=" * 60)
    print("  VOCASCADE VOICE SERVER HEALTH REPORT")
    print("=" * 60)
    print(f"Server Host/Port:   {config.host}:{config.port}")
    print(f"Audio Sample Rates: IN: {config.audio_in_sample_rate} Hz, OUT: {config.audio_out_sample_rate} Hz")
    print(f"LLM Endpoint:       {config.llm_base_url} [{llm_verdict}]")
    print(f"LLM Model:          {config.llm_model}")
    print(f"Hermes Agent:       {config.hermes_base_url or '(local-only)'} [{hermes_verdict}]")
    print(f"Hermes Model:       {config.hermes_model}")
    print(f"Hermes Session Key: {config.hermes_session_key}")
    print(f"Context Source:     {config.hermes_context_source} (poll: {config.hermes_context_poll_interval}s, budget: {config.context_token_budget} tokens)")
    print(f"Genie TTS URL:      {config.tts_url}")
    print(f"Genie Character:    {config.tts_character_name}")
    print(f"Whisper STT:        {config.whisper_model} ({config.whisper_language}, beam={config.whisper_beam_size}, vad_filter={config.whisper_vad_filter})")
    print(f"VAD Tuning:         threshold={config.vad_threshold}, min_silence={config.vad_min_silence_ms}ms, pad={config.vad_speech_pad_ms}ms")
    print(f"TTS Volume:         {config.tts_volume}x")
    print(f"Task Journal:       {config.task_journal_path}")
    print(f"Honcho API URL:     {config.honcho_api_url or '(disabled)'} (Poll interval: {config.honcho_poll_interval}s)")
    print(f"LiteLLM Health URL: {config.litellm_health_url}")
    print(f"Offline Schedule:   {config.offline_start_hour}:00 - {config.offline_end_hour}:00")
    print(f"Offline Queue Path: {config.offline_queue_path}")
    print(f"Skip Genie Init:    {config.skip_genie_init}")
    print("=" * 60)
    if llm_verdict != "OK":
        logger.warning(
            "Fast-brain LLM probe failed (%s). The server will start, but "
            "smalltalk and intent routing will degrade until it is reachable.",
            llm_verdict,
        )

def main():
    try:
        config = load_config()
        print_health_report(config)
        logger.info("Configuration loaded successfully. Starting FastAPI uvicorn server...")
        
        import uvicorn
        from vocascade.adapter import app
        
        uvicorn.run(app, host=config.host, port=config.port)
    except Exception as e:
        logger.critical(f"Failed to start voice adapter: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()

