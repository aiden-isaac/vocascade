"""
voice_adapter/filler_engine.py — Pre-rendered acknowledgement audio loader.

"Ack" / filler clips are short PCM files synthesized offline by
`scripts/generate_fillers.py` from the phrase list in `static/fillers.json`.
They are loaded into RAM at startup and pushed straight to the client for
zero-latency playback (a "Yes?" the instant the wakeword fires).

`acknowledge` (wakeword) is the only category in active use: clips played at
other moments (e.g. "working" on Hermes dispatch) race the model's own spoken
reply and create overlap, so they were dropped. The loader stays generic — it
loads any known category present on disk.

Directory layout expected under `filler_dir` (default `static/fillers/`):

    filler_dir/
      acknowledge/      # spoken when the wakeword is heard ("Yes?", "I'm listening.")
        yes.pcm
        ...

Each file is raw 16-bit mono PCM at the adapter's output sample rate (32 kHz).
"""

import logging
import random
from pathlib import Path

logger = logging.getLogger("voice_adapter.filler_engine")

# All known filler categories. Files under any other directory are ignored.
KNOWN_CATEGORIES = {
    "thinking",
    "working",
    "slow_task",
    "acknowledge",
    "signoff",
}


class FillerEngine:
    """
    Loads pre-rendered PCM acknowledgement audio from `filler_dir` at startup
    and serves random clips per category from memory.

    Usage:
        engine = FillerEngine(Path("static/fillers"))
        pcm = engine.get_filler("acknowledge")   # bytes | None
    """

    def __init__(self, filler_dir: Path) -> None:
        self._fillers: dict[str, list[bytes]] = {cat: [] for cat in KNOWN_CATEGORIES}
        self._filler_dir = Path(filler_dir)
        self._load(self._filler_dir)

    def _load(self, filler_dir: Path) -> None:
        if not filler_dir.exists():
            logger.warning(
                "FillerEngine: filler directory %s does not exist — ack audio disabled. "
                "Run `python scripts/generate_fillers.py` to create it.",
                filler_dir,
            )
            return

        total = 0
        for category in KNOWN_CATEGORIES:
            cat_dir = filler_dir / category
            if not cat_dir.is_dir():
                continue
            for pcm_file in sorted(cat_dir.glob("*.pcm")):
                data = pcm_file.read_bytes()
                if len(data) < 2:
                    logger.warning("FillerEngine: skipping empty file %s", pcm_file)
                    continue
                # Ensure even byte count (16-bit PCM requirement)
                if len(data) % 2 != 0:
                    data = data[:-1]
                self._fillers[category].append(data)
                total += 1

        logger.info(
            "FillerEngine: loaded %d clip(s) from %s — categories: %s",
            total,
            filler_dir,
            {cat: len(v) for cat, v in self._fillers.items() if v},
        )

    def loaded(self) -> bool:
        """True if at least one filler clip was loaded successfully."""
        return any(self._fillers.values())

    def get_filler(self, category: str) -> bytes | None:
        """
        Return a random PCM clip for `category`, or None if none are available.
        Falls back to the "thinking" category when the requested one is empty.
        """
        options = self._fillers.get(category) or self._fillers.get("thinking") or []
        if not options:
            return None
        return random.choice(options)

    def has_category(self, category: str) -> bool:
        return bool(self._fillers.get(category))
