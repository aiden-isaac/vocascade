"""
Canonical audio rate and format constants used across modules.
"""

CAPTURE_SAMPLE_RATE = 16_000   # VAD / STT boundary (Hz)
TTS_SAMPLE_RATE = 32_000       # TTS / playback boundary (Hz)
PCM_SAMPLE_WIDTH = 2           # 16-bit signed LE mono (bytes per sample)
