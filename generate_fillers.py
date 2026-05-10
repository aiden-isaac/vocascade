#!/usr/bin/env python3
"""
generate_fillers.py — Batch-render filler audio via the live Genie TTS server.

Connects to GENIE_TTS_URL (default http://127.0.0.1:8000), synthesizes each
filler phrase using the configured voice, and saves raw PCM files to
static/fillers/<category>/<slug>.pcm.

Usage:
    # Make sure start_servers.sh is running first, then:
    python generate_fillers.py

    # Or override the TTS URL:
    GENIE_TTS_URL=http://127.0.0.1:8000 python generate_fillers.py

The generated .pcm files are 32 kHz, 16-bit, mono (matching GENIE_SAMPLE_RATE).
They are loaded at startup by FillerEngine and served from RAM for instant
playback without a TTS round-trip.
"""

import asyncio
import os
import re
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ────────────────────────────────────────────────────────────

GENIE_TTS_URL = (os.getenv("GENIE_TTS_URL") or "http://127.0.0.1:8000").rstrip("/")
CHARACTER_NAME = os.getenv("GENIE_CHARACTER_NAME") or "fauna"
OUTPUT_DIR = Path(__file__).resolve().parent / "static" / "fillers"

# Ordis-flavoured fillers. Swap out for your character's voice patterns.
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def phrase_to_slug(phrase: str) -> str:
    """'Let me check the weave.' → 'let_me_check_the_weave'"""
    slug = phrase.lower()
    slug = re.sub(r"[^\w\s]", "", slug)
    slug = re.sub(r"\s+", "_", slug.strip())
    return slug[:64]  # cap length


async def synthesize_pcm(session: aiohttp.ClientSession, text: str) -> bytes:
    payload = {
        "character_name": CHARACTER_NAME,
        "text": text,
        "split_sentence": False,  # filler phrases are single sentences
    }
    async with session.post(f"{GENIE_TTS_URL}/tts", json=payload) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"TTS /tts failed [{resp.status}]: {body}")
        raw = bytearray()
        async for chunk in resp.content.iter_chunked(4096):
            raw.extend(chunk)
        return bytes(raw)


async def main() -> None:
    print(f"Genie TTS URL : {GENIE_TTS_URL}")
    print(f"Character     : {CHARACTER_NAME}")
    print(f"Output dir    : {OUTPUT_DIR}")
    print()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    total_ok = 0
    total_err = 0

    async with aiohttp.ClientSession() as session:
        for category, phrases in FILLER_PHRASES.items():
            cat_dir = OUTPUT_DIR / category
            cat_dir.mkdir(exist_ok=True)
            print(f"  [{category}]")

            for phrase in phrases:
                slug = phrase_to_slug(phrase)
                out_path = cat_dir / f"{slug}.pcm"
                try:
                    pcm = await synthesize_pcm(session, phrase)
                    # Ensure even byte count (16-bit PCM)
                    if len(pcm) % 2 != 0:
                        pcm = pcm[:-1]
                    out_path.write_bytes(pcm)
                    print(f"    ✓  {phrase!r:40s} → {out_path.name} ({len(pcm):,} bytes)")
                    total_ok += 1
                except Exception as exc:
                    print(f"    ✗  {phrase!r:40s} — ERROR: {exc}")
                    total_err += 1

                # Small delay between requests to avoid overwhelming the TTS server
                await asyncio.sleep(0.5)

    print()
    print(f"Done: {total_ok} OK, {total_err} errors")
    if total_err:
        print("Re-run after fixing errors. Existing .pcm files are kept intact.")


if __name__ == "__main__":
    asyncio.run(main())
