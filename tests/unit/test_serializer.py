import unittest
import base64
import json
from vocascade.transport.serializer import RawFrameSerializer
from vocascade.pipeline.pipeline import AudioFrame, ControlMessageFrame

class TestRawFrameSerializer(unittest.TestCase):
    def setUp(self):
        self.serializer = RawFrameSerializer()

    def test_serialize_audio(self):
        audio_data = b"hello_world"
        frame = AudioFrame(audio=audio_data, sample_rate=32000)
        serialized = self.serializer.serialize(frame)
        self.assertIsNotNone(serialized)
        
        parsed = json.loads(serialized)
        self.assertEqual(parsed["type"], "audio")
        self.assertEqual(parsed["sample_rate"], 32000)
        
        decoded = base64.b64decode(parsed["data"])
        self.assertEqual(decoded, audio_data)

    def test_serialize_control(self):
        msg = {"type": "status", "state": "active_listening"}
        frame = ControlMessageFrame(message=msg)
        serialized = self.serializer.serialize(frame)
        self.assertIsNotNone(serialized)
        
        parsed = json.loads(serialized)
        self.assertEqual(parsed, msg)

    def test_deserialize_bytes_to_audio(self):
        audio_data = b"incoming_audio"
        deserialized = self.serializer.deserialize(audio_data)
        self.assertIsInstance(deserialized, AudioFrame)
        self.assertEqual(deserialized.audio, audio_data)
        self.assertEqual(deserialized.sample_rate, 16000)
        self.assertEqual(deserialized.num_channels, 1)

    def test_deserialize_str_to_control(self):
        msg_str = '{"type": "wakeword"}'
        deserialized = self.serializer.deserialize(msg_str)
        self.assertIsInstance(deserialized, ControlMessageFrame)
        self.assertEqual(deserialized.message, {"type": "wakeword"})

    def test_deserialize_invalid_json(self):
        deserialized = self.serializer.deserialize("invalid_json{")
        self.assertIsNone(deserialized)

if __name__ == "__main__":
    unittest.main()

