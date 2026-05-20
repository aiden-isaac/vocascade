"""
generate_fillers.py -- Batch-render filler audio via the Genie TTS server.

Connects to Genie TTS server, synthesizes each filler phrase using the configured voice,
and saves raw PCM files to static/fillers/<category>/<slug>.pcm.
"""

import asyncio
import os
import re
import sys
from pathlib import Path

# Ensure package directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Prevent load_config from failing on missing key environment variables
os.environ.setdefault("LITELLM_API_KEY", "dummy_key")
os.environ.setdefault("OPENCLAW_GATEWAY_TOKEN", "dummy_token")

from voice_satellite.config import load_config
from voice_satellite.tts.genie_client import GenieTTSClient

# Define filler phrases per category
FILLER_PHRASES: dict[str, list[str]] = {
    "thinking": [
        "Hmm.",
        "Let me think.",
        "One moment.",
    ],
    "working": [
        "Let me check the weave.",
        "Running diagnostics.",
        "Analyzing.",
    ],
    "slow_task": [
        "This might take a moment.",
        "Working on that now.",
        "Processing, Operator.",
    ],
    "acknowledge": [
        "Yes, Operator?",
        "Ordis is listening.",
        "Go ahead, Operator.",
    ],
    "signoff": [
        "Until next time, Operator.",
        "Ordis will be here.",
        "Farewell, Operator.",
    ],
}

def phrase_to_slug(phrase: str) -> str:
    """'Let me check the weave.' -> 'let_me_check_the_weave'"""
    slug = phrase.lower()
    slug = re.sub(r"[^\w\s]", "", slug)
    slug = re.sub(r"\s+", "_", slug.strip())
    return slug[:64]

async def main() -> None:
    config = load_config()
    print(f"Genie TTS URL : {config.tts_url}")
    print(f"Character     : {config.tts_character_name}")
    print(f"Output dir    : {config.filler_dir}")
    print()

    config.filler_dir.mkdir(parents=True, exist_ok=True)

    # Initialize Genie client. Set degraded_mode to False initially so it attempts initialization.
    tts_client = GenieTTSClient(
        tts_url=config.tts_url,
        character_name=config.tts_character_name,
        onnx_model_dir=config.tts_onnx_model_dir,
        reference_audio=config.tts_reference_audio,
        reference_text=config.tts_reference_text,
        language=config.tts_language,
        degraded_mode=False
    )
    
    total_ok = 0
    total_err = 0

    for category, phrases in FILLER_PHRASES.items():
        cat_dir = config.filler_dir / category
        cat_dir.mkdir(exist_ok=True)
        print(f"  [{category}]")

        for phrase in phrases:
            slug = phrase_to_slug(phrase)
            out_path = cat_dir / f"{slug}.pcm"
            try:
                # Accumulate the chunks from tts_client.synthesize
                pcm_bytes_list = []
                async for chunk in tts_client.synthesize(phrase):
                    if chunk:
                        pcm_bytes_list.append(chunk)
                
                if not pcm_bytes_list or tts_client.degraded_mode:
                    raise RuntimeError("Received empty audio or client entered degraded mode (TTS server may be offline)")
                
                pcm = b"".join(pcm_bytes_list)
                if len(pcm) % 2 != 0:
                    pcm = pcm[:-1]
                
                out_path.write_bytes(pcm)
                print(f"    OK  {phrase!r:40s} -> {out_path.name} ({len(pcm):,} bytes)")
                total_ok += 1
            except Exception as exc:
                print(f"    ERR {phrase!r:40s} -- ERROR: {exc}")
                total_err += 1

            # Small delay between requests
            await asyncio.sleep(0.5)

    print()
    print(f"Done: {total_ok} OK, {total_err} errors")

if __name__ == "__main__":
    asyncio.run(main())
