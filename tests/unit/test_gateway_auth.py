import tempfile
import unittest
from pathlib import Path
import base64
from voice_satellite.gateway import load_or_generate_keypair, sign_challenge
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization

class TestGatewayAuth(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.key_path = Path(self.temp_dir.name) / "identity.pem"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_keypair_generation_and_loading(self):
        # 1. Generate keypair for first time
        priv, pub = load_or_generate_keypair(self.key_path)
        self.assertIsInstance(priv, Ed25519PrivateKey)
        self.assertIsInstance(pub, Ed25519PublicKey)
        self.assertTrue(self.key_path.exists())

        # 2. Load the keypair from same file path
        priv2, pub2 = load_or_generate_keypair(self.key_path)
        self.assertIsInstance(priv2, Ed25519PrivateKey)
        self.assertIsInstance(pub2, Ed25519PublicKey)
        
        # Verify they are the same public key
        raw_pub = pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
        raw_pub2 = pub2.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
        self.assertEqual(raw_pub, raw_pub2)

    def test_sign_challenge(self):
        priv, pub = load_or_generate_keypair(self.key_path)
        nonce = "test-challenge-nonce-1234"
        signature = sign_challenge(priv, nonce)
        
        # Verify the base64 signature
        sig_bytes = base64.b64decode(signature)
        self.assertEqual(len(sig_bytes), 64)
        
        # Verify using the public key (raises cryptography.exceptions.InvalidSignature if wrong)
        pub.verify(sig_bytes, nonce.encode("utf-8"))

if __name__ == "__main__":
    unittest.main()
