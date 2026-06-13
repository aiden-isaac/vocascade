"""
Ed25519 device identity and gateway challenge-response authentication.
"""

import base64
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization

def load_or_generate_keypair(path: str | Path) -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """
    Loads an Ed25519 private key from the given path.
    If the file does not exist, generates a new keypair, saves the private key PEM
    to the path (creating parent directories if needed), and returns the (private_key, public_key) pair.
    """
    key_path = Path(path)
    if key_path.exists():
        pem_data = key_path.read_bytes()
        private_key = serialization.load_pem_private_key(pem_data, password=None)
        if not isinstance(private_key, Ed25519PrivateKey):
            raise TypeError("Loaded key is not an Ed25519 private key")
        return private_key, private_key.public_key()

    # Generate keypair
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    # Serialize private key to PEM
    pem_data = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )

    # Ensure parent directories exist
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(pem_data)

    return private_key, public_key

def sign_challenge(private_key: Ed25519PrivateKey, nonce: str) -> str:
    """
    Signs the given challenge string (nonce or token payload) using the private key.
    Returns the base64-encoded signature.
    """
    signature_bytes = private_key.sign(nonce.encode("utf-8"))
    return base64.b64encode(signature_bytes).decode("utf-8")
