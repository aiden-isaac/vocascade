import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# We will implement these in satellite.py
from satellite import SatelliteClient, ClientState

@pytest.fixture
def satellite_client():
    config = {
        "ws_url": "ws://localhost:8000/ws",
        "wake_word_model": "static/wakeword/eden_wakeword.onnx",
        "audio_in_rate": 16000,
        "audio_out_rate": 32000
    }
    client = SatelliteClient(config)
    return client

def test_initial_state(satellite_client):
    assert satellite_client.state == ClientState.LISTENING

@pytest.mark.asyncio
async def test_wake_word_triggers_connection(satellite_client):
    # Mock the websocket connection and audio stream
    satellite_client._connect_ws = AsyncMock()
    
    # Simulate wake word detection
    await satellite_client.handle_wake_word_detected()
    
    assert satellite_client.state == ClientState.CONNECTING
    satellite_client._connect_ws.assert_called_once()

@pytest.mark.asyncio
async def test_silence_triggers_disconnect(satellite_client):
    satellite_client.state = ClientState.STREAMING
    satellite_client._disconnect_ws = AsyncMock()
    
    # Simulate silence detection timeout
    await satellite_client.handle_silence_timeout()
    
    assert satellite_client.state == ClientState.LISTENING
    satellite_client._disconnect_ws.assert_called_once()

@pytest.mark.asyncio
async def test_server_close_triggers_disconnect(satellite_client):
    satellite_client.state = ClientState.STREAMING
    satellite_client._disconnect_ws = AsyncMock()
    
    # Simulate server side close
    await satellite_client.handle_server_close()
    
    assert satellite_client.state == ClientState.LISTENING
    satellite_client._disconnect_ws.assert_called_once()
