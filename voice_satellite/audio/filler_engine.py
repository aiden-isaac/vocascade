"""
Pre-rendered PCM filler audio loader and playback manager.
"""

import logging
import random
from pathlib import Path

logger = logging.getLogger("voice_satellite.audio")

class FillerEngine:
    """
    Manages pre-rendered PCM fillers loaded into memory at startup.
    Supports random selection within categories with automatic fallback to 'thinking' category.
    """
    def __init__(self, filler_dir: str | Path) -> None:
        self.filler_dir = Path(filler_dir)
        self.fillers = {
            "thinking": [],
            "working": [],
            "acknowledge": [],
            "slow_task": [],
            "signoff": []
        }
        self.load_fillers()

    def load_fillers(self) -> int:
        """
        Scans expected category subdirectories, reads all .pcm files,
        and loads their raw byte contents into memory.
        """
        total_loaded = 0
        if not self.filler_dir.exists() or not self.filler_dir.is_dir():
            logger.warning(f"Filler directory '{self.filler_dir}' does not exist. Starting with empty fillers.")
            return 0

        for category in self.fillers.keys():
            category_dir = self.filler_dir / category
            if not category_dir.exists() or not category_dir.is_dir():
                logger.warning(f"Category directory '{category_dir}' does not exist.")
                continue

            for pcm_file in category_dir.glob("*.pcm"):
                if pcm_file.is_file():
                    try:
                        content = pcm_file.read_bytes()
                        if content:
                            self.fillers[category].append(content)
                            total_loaded += 1
                    except Exception as e:
                        logger.error(f"Error loading filler '{pcm_file}': {e}")

        logger.info(f"Loaded {total_loaded} filler files into RAM: {self.get_categories()}")
        return total_loaded

    def get_filler(self, category: str) -> bytes | None:
        """
        Returns random PCM bytes from the specified category.
        Falls back to 'thinking' if requested category is empty, and returns None if neither has audio.
        """
        pcm_list = self.fillers.get(category, [])
        if pcm_list:
            return random.choice(pcm_list)

        # Fallback to thinking
        fallback_list = self.fillers.get("thinking", [])
        if fallback_list:
            logger.debug(f"Filler category '{category}' not found or empty, falling back to 'thinking'")
            return random.choice(fallback_list)

        return None

    def get_categories(self) -> dict[str, int]:
        """
        Returns the count of loaded fillers in each category.
        """
        return {cat: len(files) for cat, files in self.fillers.items()}
