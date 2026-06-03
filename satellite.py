import asyncio
import logging
import time
from enum import Enum
import pyaudio
import websockets
import openwakeword
from openwakeword.model import Model
import numpy as np
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("satellite")

class ClientState(Enum):
    LISTENING = "LISTENING"
    CONNECTING = "CONNECTING"
    STREAMING = "STREAMING"

class SatelliteClient:
    def __init__(self, config):
        self.config = config
        self.state = ClientState.LISTENING
        self.ws = None
        
        # Audio config
        self.chunk_size = 1280  # Suitable chunk size for openWakeWord
        self.audio_in_rate = config.get("audio_in_rate", 16000)
        self.audio_out_rate = config.get("audio_out_rate", 32000)
        
        self.p = pyaudio.PyAudio()
        self.stream_in = None
        self.stream_out = None
        
        # Load openWakeWord
        model_path = config.get("wake_word_model")
        if model_path and os.path.exists(model_path):
            self.oww_model = Model(wakeword_model_paths=[model_path])
            logger.info(f"Loaded wake word model: {model_path}")
        else:
            self.oww_model = None
            logger.warning(f"Wake word model not found at {model_path}. Wake word detection will be disabled.")
            
        self.ws_url = config.get("ws_url", "ws://localhost:8000/ws")
        self.last_audio_time = time.time()
        
    def start_audio(self):
        try:
            self.stream_in = self.p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.audio_in_rate,
                input=True,
                frames_per_buffer=self.chunk_size
            )
            self.stream_out = self.p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.audio_out_rate,
                output=True,
                frames_per_buffer=self.chunk_size
            )
            logger.info("Audio streams initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize audio streams: {e}")

    def stop_audio(self):
        if self.stream_in:
            self.stream_in.stop_stream()
            self.stream_in.close()
        if self.stream_out:
            self.stream_out.stop_stream()
            self.stream_out.close()
        self.p.terminate()

    async def handle_wake_word_detected(self):
        logger.info("Wake word detected! Transitioning to CONNECTING.")
        self.state = ClientState.CONNECTING
        await self._connect_ws()
        
    async def _connect_ws(self):
        try:
            self.ws = await websockets.connect(self.ws_url)
            self.state = ClientState.STREAMING
            self.last_audio_time = time.time()
            logger.info("Connected to WebSocket, transitioning to STREAMING.")
            
            # Start concurrent tasks for reading/writing WS
            asyncio.create_task(self._ws_receive_loop())
            asyncio.create_task(self._ws_send_loop())
            
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            self.state = ClientState.LISTENING

    async def _disconnect_ws(self):
        self.state = ClientState.LISTENING
        if self.ws:
            await self.ws.close()
            self.ws = None
        logger.info("Disconnected from WebSocket. Returning to LISTENING.")

    async def handle_silence_timeout(self):
        logger.info("Silence timeout reached. Disconnecting.")
        self.state = ClientState.LISTENING
        await self._disconnect_ws()

    async def handle_server_close(self):
        logger.info("Server closed connection. Disconnecting.")
        self.state = ClientState.LISTENING
        await self._disconnect_ws()

    async def _ws_receive_loop(self):
        try:
            while self.state == ClientState.STREAMING and self.ws:
                message = await self.ws.recv()
                if isinstance(message, bytes):
                    # Write PCM audio to speaker in a thread
                    if self.stream_out:
                        await asyncio.to_thread(self.stream_out.write, message)
                self.last_audio_time = time.time()
        except websockets.exceptions.ConnectionClosed:
            await self.handle_server_close()
        except Exception as e:
            logger.error(f"Error in receive loop: {e}")
            await self.handle_server_close()

    async def _ws_send_loop(self):
        try:
            CHUNK_SAMPLES = 4096          # 256ms at 16kHz
            while self.state == ClientState.STREAMING and self.ws:
                if self.stream_in:
                    # Read mic in a thread
                    data = await asyncio.to_thread(self.stream_in.read, CHUNK_SAMPLES, False)
                    if self.state == ClientState.STREAMING and self.ws:
                        await self.ws.send(data)
                
                # Check silence timeout (e.g. 15 seconds of no audio out/in activity)
                if time.time() - self.last_audio_time > 15.0:
                    await self.handle_silence_timeout()
                    break
        except Exception as e:
            logger.error(f"Error in send loop: {e}")
            await self.handle_server_close()

    async def run_loop(self):
        self.start_audio()
        logger.info("Starting satellite listening loop...")
        
        try:
            while True:
                if self.state == ClientState.LISTENING:
                    if not self.stream_in:
                        await asyncio.sleep(1)
                        continue
                        
                    # Read audio and detect wake word in a thread
                    pcm = await asyncio.to_thread(self.stream_in.read, self.chunk_size, False)
                    
                    if self.oww_model:
                        audio_data = np.frombuffer(pcm, dtype=np.int16)
                        prediction = self.oww_model.predict(audio_data)
                        
                        # Check prediction buffer for wake word match
                        for mdl in self.oww_model.prediction_buffer.keys():
                            scores = list(self.oww_model.prediction_buffer[mdl])
                            if scores and scores[-1] > 0.5:
                                # Clear buffer to prevent double triggers
                                self.oww_model.reset()
                                await self.handle_wake_word_detected()
                                break
                    await asyncio.sleep(0.01)
                else:
                    # In CONNECTING or STREAMING state, processing is handled by asyncio tasks
                    await asyncio.sleep(0.1)
        except KeyboardInterrupt:
            logger.info("Stopping satellite client...")
        finally:
            self.stop_audio()
            if self.ws:
                await self.ws.close()

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    config = {
        "ws_url": os.getenv("WS_URL", "ws://localhost:8000/ws"),
        "wake_word_model": os.getenv("WAKE_WORD_MODEL", "static/wakeword/eden_wakeword.onnx"),
        "audio_in_rate": int(os.getenv("AUDIO_IN_SAMPLE_RATE", 16000)),
        "audio_out_rate": int(os.getenv("AUDIO_OUT_SAMPLE_RATE", 32000))
    }
    client = SatelliteClient(config)
    asyncio.run(client.run_loop())
