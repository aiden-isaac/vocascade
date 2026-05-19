"""
voice_satellite/filler_engine.py — Pre-rendered filler audio loader.

Fillers are short PCM audio files synthesized offline by generate_fillers.py.
They are loaded at startup and served from RAM for zero-latency playback.

Directory layout expected under `filler_dir`:
  filler_dir/
    thinking/
      hmm.pcm
      one_moment.pcm
      let_me_think.pcm
    working/
      let_me_check_the_weave.pcm
      analyzing.pcm
      running_diagnostics.pcm
    slow_task/
      this_might_take_a_moment.pcm
      working_on_that_now.pcm
    acknowledge/
      yes_operator.pcm
      ordis_is_listening.pcm
      go_ahead_operator.pcm
    signoff/
      ill_see_you_next_time.pcm
      until_next_time.pcm
"""

import logging
import random
from pathlib import Path

logger = logging.getLogger(__name__)

# All known filler categories. Any .pcm file under an unknown directory is ignored.
KNOWN_CATEGORIES = {
    "thinking",
    "working",
    "slow_task",
    "acknowledge",
    "signoff",
}


class FillerEngine:
    """
    Loads pre-rendered PCM filler audio from `filler_dir` at startup.

    Usage:
        engine = FillerEngine(Path("static/fillers"))
        pcm = engine.get_filler("thinking")   # bytes | None
        if pcm:
            # send to frontend as-is (already 32kHz 16-bit mono PCM)
            ...
    """

    def __init__(self, filler_dir: Path) -> None:
        self._fillers: dict[str, list[bytes]] = {cat: [] for cat in KNOWN_CATEGORIES}
        self._filler_dir = filler_dir
        self._load(filler_dir)

    def _load(self, filler_dir: Path) -> None:
        if not filler_dir.exists():
            logger.warning(
                "FillerEngine: filler directory %s does not exist — fillers disabled",
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
                logger.debug(
                    "FillerEngine: loaded %s/%s (%d bytes)",
                    category,
                    pcm_file.name,
                    len(data),
                )

        logger.info(
            "FillerEngine: loaded %d filler(s) from %s — categories: %s",
            total,
            filler_dir,
            {cat: len(v) for cat, v in self._fillers.items() if v},
        )

    def loaded(self) -> bool:
        """True if at least one filler was loaded successfully."""
        return any(len(v) > 0 for v in self._fillers.values())

    def get_filler(self, category: str) -> bytes | None:
        """
        Return a random filler PCM from the given category, or None if
        none are loaded. Falls back to "thinking" if the category is empty.
        """
        options = self._fillers.get(category, [])
        if not options:
            # Fallback to thinking
            options = self._fillers.get("thinking", [])
        if not options:
            return None
        return random.choice(options)

    def has_category(self, category: str) -> bool:
        return bool(self._fillers.get(category))

    def category_count(self, category: str) -> int:
        return len(self._fillers.get(category, []))
