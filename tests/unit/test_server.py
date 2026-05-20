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

if __name__ == "__main__":
    unittest.main()
