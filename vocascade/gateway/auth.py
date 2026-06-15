"""
Ed25519 device identity and gateway challenge-response authentication.

The edge holds a private key (``load_or_generate_keypair``) and signs the
server's nonce (``sign_challenge``). The server presents that nonce, then
verifies the returned signature against the presented public key
(``verify_signature``) and — when an allowlist is configured — checks the key's
fingerprint against the trust store (``load_authorized_keys``). This is the
device-identity half of OQ-3; the trust-network mode skips it entirely.
"""

import base64
import logging
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization

logger = logging.getLogger("vocascade.gateway.auth")

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


def public_key_to_b64(public_key: Ed25519PublicKey) -> str:
    """Encode a public key as base64 of its 32-byte raw form (the wire/trust-store form)."""
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("utf-8")


def public_key_from_b64(b64: str) -> Ed25519PublicKey:
    """Decode a base64 raw Ed25519 public key. Raises ValueError on malformed input."""
    try:
        raw = base64.b64decode(b64, validate=True)
        return Ed25519PublicKey.from_public_bytes(raw)
    except Exception as e:  # binascii.Error, ValueError from bad length, etc.
        raise ValueError(f"Malformed Ed25519 public key: {e}") from e


def verify_signature(public_key_b64: str, nonce: str, signature_b64: str) -> bool:
    """
    Server side of the challenge-response: verify that ``signature_b64`` is a valid
    Ed25519 signature of ``nonce`` by the holder of ``public_key_b64``. Returns
    True only on a cryptographically valid signature; any malformed input or
    mismatch returns False (never raises).
    """
    try:
        public_key = public_key_from_b64(public_key_b64)
        signature = base64.b64decode(signature_b64, validate=True)
        public_key.verify(signature, nonce.encode("utf-8"))
        return True
    except (InvalidSignature, ValueError):
        return False
    except Exception as e:  # defensive: never let a verify failure crash the gate
        logger.warning("Unexpected error verifying device signature: %s", e)
        return False


def load_authorized_keys(path: str | Path | None) -> set[str]:
    """
    Load the trust store: a set of base64 raw Ed25519 public keys, one per line,
    with ``#`` comments and blank lines ignored. A missing path or file yields an
    empty set (no allowlist configured — callers decide the policy for that case).
    """
    if not path:
        return set()
    key_path = Path(path)
    if not key_path.exists():
        return set()
    keys: set[str] = set()
    for raw_line in key_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Tolerate "<b64key>  optional comment/label" lines.
        keys.add(line.split()[0])
    return keys
