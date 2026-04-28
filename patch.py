import re

with open("/home/aiden/voice-satellite/server.py", "r") as f:
    content = f.read()

replacement = """            data = await websocket.receive()
            if data.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(data.get("code", 1000))
            if "bytes" in data:"""

content = content.replace('            data = await websocket.receive()\n            if "bytes" in data:', replacement)

with open("/home/aiden/voice-satellite/server.py", "w") as f:
    f.write(content)
