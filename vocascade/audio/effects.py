"""
Audio post-processing effects for TTS streaming.
All individual effects operate on numpy int16 arrays at TTS_SAMPLE_RATE.
"""

import numpy as np
from vocascade.audio.constants import TTS_SAMPLE_RATE

def apply_pitch_shift(pcm: np.ndarray, semitones: float) -> np.ndarray:
    """
    Apply linear resampling pitch shift to a numpy int16 array.
    Positive semitones shift pitch up, negative semitones shift down.
    """
    if semitones == 0.0 or semitones == 0:
        return pcm
    # Frequency ratio calculation from semitones
    pitch_factor = 2.0 ** (semitones / 12.0)
    indices = np.arange(0, len(pcm), pitch_factor)
    floor = np.floor(indices).astype(int)
    ceil = np.minimum(floor + 1, len(pcm) - 1)
    weight = indices - floor
    return (pcm[floor] * (1.0 - weight) + pcm[ceil] * weight).astype(np.int16)

def apply_tremolo(pcm: np.ndarray, rate: float, depth: float) -> np.ndarray:
    """
    Apply tremolo amplitude modulation to a numpy int16 array.
    rate: frequency of modulation in Hz.
    depth: intensity of tremolo (0.0 to 1.0).
    """
    if depth <= 0.0 or rate <= 0.0:
        return pcm
    t = np.arange(len(pcm)) / float(TTS_SAMPLE_RATE)
    mod = np.sin(2 * np.pi * rate * t)
    modulation = (1.0 - depth) + depth * (0.5 + 0.5 * mod)
    return (pcm * modulation).astype(np.int16)

def apply_overdrive(pcm: np.ndarray, gain: float) -> np.ndarray:
    """
    Apply overdrive distortion clipping to a numpy int16 array.
    gain: amplification factor (> 1.0).
    """
    if gain <= 1.0:
        return pcm
    pcm_32 = pcm.astype(np.int32) * gain
    return np.clip(pcm_32, -32768, 32767).astype(np.int16)

def apply_bitcrush(pcm: np.ndarray, bit_depth: int) -> np.ndarray:
    """
    Apply bit depth reduction crush effect to a numpy int16 array.
    bit_depth: target bits (e.g., 1 to 15, 16 is no effect).
    """
    if bit_depth >= 16 or bit_depth <= 0:
        return pcm
    bit_shift = 16 - bit_depth
    factor = 2 ** bit_shift
    return ((pcm // factor) * factor).astype(np.int16)

def apply_stutter(pcm: np.ndarray, chunk_ms: float, repeats: int) -> np.ndarray:
    """
    Apply stutter loop/repeat effect to a numpy int16 array.
    chunk_ms: duration of the repeated segment.
    repeats: number of times to repeat the chunk.
    """
    if chunk_ms <= 0.0 or repeats <= 0:
        return pcm
    stutter_samples = int(TTS_SAMPLE_RATE * (chunk_ms / 1000.0))
    start_idx = min(stutter_samples, len(pcm) // 4)
    if len(pcm) > start_idx + stutter_samples * (repeats + 1):
        stutter_chunk = pcm[start_idx : start_idx + stutter_samples].copy()
        for i in range(repeats):
            insert_idx = start_idx + stutter_samples * (i + 1)
            pcm[insert_idx : insert_idx + stutter_samples] = stutter_chunk
    return pcm

def resample_pcm(pcm_bytes: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-resample mono s16le PCM bytes from src_rate to dst_rate Hz.
    Used to normalize a TTS backend's native rate (e.g. Piper's 22050) to the
    wire rate before effects/transport."""
    # ponytail: per-chunk linear interpolation — inaudible for speech upsampling;
    # switch to soxr/scipy with cross-chunk state if quality ever matters.
    if src_rate == dst_rate or not pcm_bytes:
        return pcm_bytes
    if len(pcm_bytes) % 2 != 0:
        pcm_bytes = pcm_bytes[:-1]
    samples = np.frombuffer(pcm_bytes, dtype=np.int16)
    if len(samples) < 2:
        return pcm_bytes
    n_out = int(round(len(samples) * dst_rate / src_rate))
    if n_out <= 0:
        return b""
    positions = np.linspace(0, len(samples) - 1, n_out)
    floor = np.floor(positions).astype(int)
    ceil = np.minimum(floor + 1, len(samples) - 1)
    weight = positions - floor
    out = samples[floor] * (1.0 - weight) + samples[ceil] * weight
    return out.astype(np.int16).tobytes()


def apply_gain(pcm_bytes: bytes, gain: float) -> bytes:
    """Scale loudness of raw int16 PCM bytes by `gain` (1.0 = unchanged), clipping
    to avoid wraparound. Genie has no volume param, so this is the volume knob."""
    if gain == 1.0 or not pcm_bytes:
        return pcm_bytes
    if len(pcm_bytes) % 2 != 0:
        pcm_bytes = pcm_bytes[:-1]
    arr = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) * gain
    return np.clip(arr, -32768, 32767).astype(np.int16).tobytes()


def apply_effect_chain(pcm_bytes: bytes, effects_config: dict) -> bytes:
    """
    De-serialize raw PCM bytes to numpy int16 array, compose multiple effects from a config dict,
    and serialize back to PCM bytes.
    """
    if not pcm_bytes:
        return pcm_bytes
    if len(pcm_bytes) % 2 != 0:
        pcm_bytes = pcm_bytes[:-1]
    
    arr = np.frombuffer(pcm_bytes, dtype=np.int16).copy()
    
    # 1. Pitch Shift
    semitones = effects_config.get("pitch_shift", 0.0)
    if semitones != 0.0:
        arr = apply_pitch_shift(arr, semitones)
        
    # 2. Tremolo
    tremolo_rate = effects_config.get("tremolo_rate", 0.0)
    tremolo_depth = effects_config.get("tremolo_depth", 0.0)
    if tremolo_rate > 0.0 and tremolo_depth > 0.0:
        arr = apply_tremolo(arr, tremolo_rate, tremolo_depth)
        
    # 3. Overdrive
    overdrive_gain = effects_config.get("overdrive_gain", 1.0)
    if overdrive_gain > 1.0:
        arr = apply_overdrive(arr, overdrive_gain)
        
    # 4. Bitcrush
    bit_depth = effects_config.get("bit_depth", 16)
    if bit_depth < 16:
        arr = apply_bitcrush(arr, bit_depth)
        
    # 5. Stutter
    stutter_ms = effects_config.get("stutter_ms", 0.0)
    stutter_repeats = effects_config.get("stutter_repeats", 0)
    if stutter_ms > 0.0 and stutter_repeats > 0:
        arr = apply_stutter(arr, stutter_ms, stutter_repeats)
        
    return arr.tobytes()

import math
import random

def get_character_effects_config(character_name: str) -> dict:
    """
    Get resolved character effects configuration. For Ordis, returns a randomized configuration
    mimicking the legacy Ordis glitch parameters.
    """
    if not character_name:
        return {}
    name = character_name.lower().strip()
    if name in ("ordis", "default"):
        pitch_factor = random.uniform(0.55, 0.63)
        semitones = 12.0 * math.log2(pitch_factor)
        return {
            "pitch_shift": round(semitones, 3),
            "tremolo_rate": round(random.uniform(8.0, 14.0), 1),
            "tremolo_depth": 0.5,
            "overdrive_gain": round(random.uniform(2.5, 4.5), 1),
            "bit_depth": 16 - random.randint(1, 4),
            "stutter_ms": float(random.randint(70, 120)),
            "stutter_repeats": random.randint(2, 4)
        }
    return {}

