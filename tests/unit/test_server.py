import json
import unittest
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect
from voice_satellite.server import app

class TestServer(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_index_route(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])

    def test_single_session_websocket_enforcement(self):
        # Open first connection
        with self.client.websocket_connect("/ws") as ws1:
            # Try opening a second connection concurrently
            with self.client.websocket_connect("/ws") as ws2:
                # Should receive error message
                data = ws2.receive_json()
                self.assertEqual(data, {
                    "type": "error",
                    "message": "Session already active. Please wait."
                })
                # It should raise WebSocketDisconnect or close when we try to interact or read next
                with self.assertRaises(WebSocketDisconnect):
                    ws2.receive_json()

    def test_websocket_wakeword_and_pcm(self):
        with self.client.websocket_connect("/ws") as ws:
            # 1. Check initial state
            msg1 = ws.receive_json()
            self.assertEqual(msg1, {"type": "status", "state": "passive_listening"})
            
            # 2. Send set_timeout
            ws.send_json({"type": "set_timeout", "seconds": 45.0})
            
            # 3. Send wakeword
            ws.send_json({"type": "wakeword"})
            
            # 4. Check acknowledging state
            msg2 = ws.receive_json()
            self.assertEqual(msg2, {"type": "status", "state": "acknowledging"})
            
            # 5. Check active_listening state
            msg3 = ws.receive_json()
            self.assertEqual(msg3, {"type": "status", "state": "active_listening"})
            
            # 6. Send binary PCM (should not raise error or disconnect)
            ws.send_bytes(b"some_pcm_bytes")

if __name__ == "__main__":
    unittest.main()
