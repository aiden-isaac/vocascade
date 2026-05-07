import asyncio
import io
import os
import re
import wave
from typing import AsyncIterator

import aiohttp
import numpy as np
import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, Response

app = FastAPI()

GENIE_URL = os.getenv("GENIE_TTS_URL", "http://127.0.0.1:8000")

def apply_custom_glitch(
    pcm_bytes: bytes,
    overdrive: float = 4.0,
    bit_shift: int = 8,
    stutter_ms: int = 50,
    stutter_count: int = 1,
    downsample: int = 1,
    pitch_factor: float = 1.0,
    tremolo_hz: float = 0.0
) -> bytes:
    if not pcm_bytes:
        return pcm_bytes
    
    if len(pcm_bytes) % 2 != 0:
        pcm_bytes = pcm_bytes[:-1]
        
    arr = np.frombuffer(pcm_bytes, dtype=np.int16).copy()
    
    # 0. Pitch Shift (Resampling)
    if pitch_factor != 1.0:
        indices = np.arange(0, len(arr), pitch_factor)
        floor = np.floor(indices).astype(int)
        ceil = np.minimum(floor + 1, len(arr) - 1)
        weight = indices - floor
        arr = (arr[floor] * (1 - weight) + arr[ceil] * weight).astype(np.int16)
        
    # 0.5 Tremolo / Ring Modulation (Growl effect)
    if tremolo_hz > 0:
        t = np.arange(len(arr)) / 32000.0
        mod = np.sin(2 * np.pi * tremolo_hz * t)
        arr = (arr * (0.5 + 0.5 * mod)).astype(np.int16)
    
    # 1. Overdrive
    arr_32 = arr.astype(np.int32) * overdrive
    arr = np.clip(arr_32, -32768, 32767).astype(np.int16)
    
    # 2. Bitcrush
    if bit_shift > 0:
        arr = (arr >> int(bit_shift)) << int(bit_shift)
        
    # 3. Downsample (Sample rate reduction)
    if downsample > 1:
        downsample = int(downsample)
        sub = arr[::downsample]
        arr = np.repeat(sub, downsample)[:len(arr)]
        
    # 4. Stutter
    if stutter_ms > 0 and stutter_count > 0:
        stutter_samples = int(32000 * (stutter_ms / 1000.0))
        start_idx = min(stutter_samples, len(arr) // 4) 
        if len(arr) > start_idx + stutter_samples * (stutter_count + 1):
            stutter_chunk = arr[start_idx : start_idx + stutter_samples].copy()
            for i in range(int(stutter_count)):
                insert_idx = start_idx + stutter_samples * (i + 1)
                arr[insert_idx : insert_idx + stutter_samples] = stutter_chunk

    return arr.tobytes()

async def synthesize_genie(text: str) -> bytes:
    character_name = os.getenv("GENIE_CHARACTER_NAME", "ordis")
    payload = {
        "character_name": character_name,
        "text": text,
        "split_sentence": True,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{GENIE_URL}/tts", json=payload) as response:
            if response.status != 200:
                raise RuntimeError(f"Genie TTS failed: {await response.text()}")
            
            buffer = bytearray()
            async for chunk in response.content.iter_chunked(4096):
                if chunk:
                    buffer.extend(chunk)
            return bytes(buffer)

@app.get("/")
def index():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Ordis Glitch Tuner</title>
        <style>
            body { background: #08110d; color: #d6ffe5; font-family: monospace; padding: 20px; }
            .container { max-width: 800px; margin: 0 auto; background: #0d1c15; padding: 20px; border-radius: 8px; border: 1px solid #1f6f43; }
            label { display: block; margin-top: 15px; font-weight: bold; }
            input[type="range"] { width: 100%; margin-top: 5px; }
            .val { float: right; color: #93bda2; }
            textarea { width: 100%; height: 80px; background: #000; color: #fff; border: 1px solid #1f6f43; padding: 10px; margin-top: 5px; }
            button { background: #1f6f43; color: #fff; border: none; padding: 10px 20px; margin-top: 20px; cursor: pointer; border-radius: 4px; width: 100%; font-size: 16px; }
            button:hover { background: #2a8f57; }
            audio { width: 100%; margin-top: 20px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h2>Ordis Glitch Tuner</h2>
            
            <label>Test Text (Wrap glitch in &lt;glitch&gt; tags):</label>
            <textarea id="text">Ordis will <glitch>— PURGE THEM ALL —</glitch> uh, Ordis will clean that right up.</textarea>
            
            <label>Overdrive (Volume multiplier & clipping) <span class="val" id="ov_val">4.0</span></label>
            <input type="range" id="overdrive" min="1.0" max="20.0" step="0.5" value="4.0" oninput="document.getElementById('ov_val').innerText = this.value">
            
            <label>Pitch Shift (Resampling rate, &lt; 1 = deeper/slower) <span class="val" id="ps_val">0.6</span></label>
            <input type="range" id="pitch_factor" min="0.2" max="2.0" step="0.05" value="0.6" oninput="document.getElementById('ps_val').innerText = this.value">
            
            <label>Tremolo (Growl frequency, Hz) <span class="val" id="tr_val">30</span></label>
            <input type="range" id="tremolo_hz" min="0" max="100" step="5" value="30" oninput="document.getElementById('tr_val').innerText = this.value">
            
            <label>Bit Shift (Bitcrush depth, 0-15) <span class="val" id="bs_val">8</span></label>
            <input type="range" id="bit_shift" min="0" max="15" step="1" value="8" oninput="document.getElementById('bs_val').innerText = this.value">
            
            <label>Downsample (Reduce sample rate, 1-20) <span class="val" id="ds_val">1</span></label>
            <input type="range" id="downsample" min="1" max="20" step="1" value="1" oninput="document.getElementById('ds_val').innerText = this.value">
            
            <label>Stutter Length (ms) <span class="val" id="sl_val">50</span></label>
            <input type="range" id="stutter_ms" min="0" max="200" step="10" value="50" oninput="document.getElementById('sl_val').innerText = this.value">
            
            <label>Stutter Count (repeats) <span class="val" id="sc_val">1</span></label>
            <input type="range" id="stutter_count" min="0" max="10" step="1" value="1" oninput="document.getElementById('sc_val').innerText = this.value">
            
            <button onclick="generate()">Generate Audio</button>
            
            <audio id="player" controls></audio>
        </div>

        <script>
            function generate() {
                const text = encodeURIComponent(document.getElementById('text').value);
                const overdrive = document.getElementById('overdrive').value;
                const pitch_factor = document.getElementById('pitch_factor').value;
                const tremolo_hz = document.getElementById('tremolo_hz').value;
                const bit_shift = document.getElementById('bit_shift').value;
                const downsample = document.getElementById('downsample').value;
                const stutter_ms = document.getElementById('stutter_ms').value;
                const stutter_count = document.getElementById('stutter_count').value;
                
                const url = `/synthesize?text=${text}&overdrive=${overdrive}&pitch_factor=${pitch_factor}&tremolo_hz=${tremolo_hz}&bit_shift=${bit_shift}&downsample=${downsample}&stutter_ms=${stutter_ms}&stutter_count=${stutter_count}`;
                
                const player = document.getElementById('player');
                player.src = url;
                player.play();
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

@app.get("/synthesize")
async def synthesize_api(
    text: str,
    overdrive: float = 4.0,
    pitch_factor: float = 1.0,
    tremolo_hz: float = 0.0,
    bit_shift: int = 8,
    downsample: int = 1,
    stutter_ms: int = 50,
    stutter_count: int = 1
):
    # Split the text
    pattern = r"(<glitch>.*?</glitch>|[^<>]+?[.!?](?:\s+|$))"
    parts = re.split(pattern, text, flags=re.DOTALL | re.IGNORECASE)
    parts = [p.strip() for p in parts if p.strip()]
    
    final_pcm = bytearray()
    
    for part in parts:
        is_glitch = part.startswith("<glitch>")
        clean_text = part.replace("<glitch>", "").replace("</glitch>", "").strip()
        if not clean_text: continue
            
        try:
            pcm_chunk = await synthesize_genie(clean_text)
            if is_glitch:
                pcm_chunk = apply_custom_glitch(
                    pcm_chunk, 
                    overdrive=overdrive, 
                    bit_shift=bit_shift, 
                    downsample=downsample,
                    stutter_ms=stutter_ms, 
                    stutter_count=stutter_count,
                    pitch_factor=pitch_factor,
                    tremolo_hz=tremolo_hz
                )
            final_pcm.extend(pcm_chunk)
        except Exception as e:
            print(f"Failed on part '{part}': {e}")
            
    # Convert raw PCM to WAV so browser can play it
    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2) # 16-bit
        wav_file.setframerate(32000) # Genie default
        wav_file.writeframes(final_pcm)
        
    return Response(content=wav_io.getvalue(), media_type="audio/wav")

if __name__ == "__main__":
    print("Starting Glitch Tuner on http://0.0.0.0:8002")
    uvicorn.run(app, host="0.0.0.0", port=8002)
