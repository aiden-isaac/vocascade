"""
Pre-rendered PCM filler audio loader and playback manager.
"""
from pathlib import Path

class FillerEngine:
    def __init__(self, filler_dir: Path):
        self.filler_dir = Path(filler_dir)

    def load_fillers(self) -> int:
        # Placeholder loader returning a mock count of fillers
        return 14
