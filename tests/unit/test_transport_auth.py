"""
tests/unit/test_transport_auth.py — Transport auth gate (US8 / OQ-3, T242).

The endpoint must carry an *explicit* auth decision (FR-111): trust-network
passes through, device-identity runs an Ed25519 challenge-response, and an
unknown mode fails fast rather than defaulting to an open endpoint. The edge
client (`perform_client_handshake`) is exercised end-to-end against the server
gate over an in-memory duplex link.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase

from vocascade.transport.server import TransportAuth, AuthResult, TRUST_NETWORK, DEVICE_IDENTITY
from vocascade.edge.__main__ import perform_client_handshake, AuthError
from vocascade.gateway.auth import (
    load_or_generate_keypair, public_key_to_b64, verify_signature, sign_challenge,
    load_authorized_keys,
)


# --- in-memory duplex bridging server send_json/receive_json <-> client send/recv ---

class _ServerWS:
    def __init__(self, to_client, to_server):
        self._to_client, self._to_server = to_client, to_server

    async def send_json(self, obj):
        await self._to_client.put(obj)

    async def receive_json(self):
        return json.loads(await self._to_server.get())


class _ClientWS:
    def __init__(self, to_client, to_server):
        self._to_client, self._to_server = to_client, to_server

    async def recv(self):
        return json.dumps(await self._to_client.get())

    async def send(self, s):
        await self._to_server.put(s)


def _linked():
    to_client, to_server = asyncio.Queue(), asyncio.Queue()
    return _ServerWS(to_client, to_server), _ClientWS(to_client, to_server)


# --- scripted server-side double for negative cases --------------------------

class _ScriptedWS:
    def __init__(self, response):
        self.response = response          # dict returned by receive_json, or Exception
        self.sent = []

    async def send_json(self, obj):
        self.sent.append(obj)

    async def receive_json(self):
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def last_type(self):
        return self.sent[-1]["type"] if self.sent else None


# --- fail-fast: never default to open ----------------------------------------

class TestModeValidation(TestCase):
    def test_unknown_mode_raises_at_construction(self):
        with self.assertRaises(ValueError):
            TransportAuth("open")
        with self.assertRaises(ValueError):
            TransportAuth("")

    def test_valid_modes_construct(self):
        self.assertEqual(TransportAuth(TRUST_NETWORK).mode, TRUST_NETWORK)
        self.assertEqual(TransportAuth(DEVICE_IDENTITY).mode, DEVICE_IDENTITY)


# --- trust-network: pass-through, no challenge -------------------------------

class TestTrustNetwork(IsolatedAsyncioTestCase):
    async def test_passes_without_any_handshake(self):
        gate = TransportAuth(TRUST_NETWORK)
        ws = _ScriptedWS(response={})
        result = await gate.authenticate(ws)
        self.assertTrue(result.ok)
        self.assertEqual(ws.sent, [])          # no challenge sent

    async def test_client_handshake_is_noop(self):
        # No ws interaction should occur for trust-network.
        self.assertTrue(await perform_client_handshake(None, TRUST_NETWORK, "unused"))


# --- device-identity: full end-to-end roundtrip ------------------------------

class TestDeviceIdentityRoundtrip(IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.key = str(Path(self.tmp.name) / "id.pem")

    def tearDown(self):
        self.tmp.cleanup()

    async def test_valid_handshake_accepted_no_allowlist(self):
        gate = TransportAuth(DEVICE_IDENTITY, authorized_keys_path=None)
        server_ws, client_ws = _linked()
        server_res, client_res = await asyncio.gather(
            gate.authenticate(server_ws),
            perform_client_handshake(client_ws, DEVICE_IDENTITY, self.key),
        )
        self.assertTrue(server_res.ok)
        self.assertTrue(client_res)
        # device_id is the edge's public key (TOFU — proven but unpinned).
        _, pub = load_or_generate_keypair(self.key)
        self.assertEqual(server_res.device_id, public_key_to_b64(pub))

    async def test_allowlisted_device_accepted(self):
        _, pub = load_or_generate_keypair(self.key)
        allow = Path(self.tmp.name) / "authorized_keys"
        allow.write_text(f"# my edge\n{public_key_to_b64(pub)}\n", encoding="utf-8")

        gate = TransportAuth(DEVICE_IDENTITY, authorized_keys_path=str(allow))
        server_ws, client_ws = _linked()
        server_res, _ = await asyncio.gather(
            gate.authenticate(server_ws),
            perform_client_handshake(client_ws, DEVICE_IDENTITY, self.key),
        )
        self.assertTrue(server_res.ok)

    async def test_device_not_in_allowlist_rejected(self):
        # Allowlist contains a *different* device's key.
        other_key = str(Path(self.tmp.name) / "other.pem")
        _, other_pub = load_or_generate_keypair(other_key)
        allow = Path(self.tmp.name) / "authorized_keys"
        allow.write_text(public_key_to_b64(other_pub) + "\n", encoding="utf-8")

        gate = TransportAuth(DEVICE_IDENTITY, authorized_keys_path=str(allow))
        server_ws, client_ws = _linked()

        async def client():
            with self.assertRaises(AuthError):
                await perform_client_handshake(client_ws, DEVICE_IDENTITY, self.key)

        server_res, _ = await asyncio.gather(gate.authenticate(server_ws), client())
        self.assertFalse(server_res.ok)
        self.assertIn("trust store", server_res.reason)


# --- device-identity: protocol/negative cases (scripted) ---------------------

class TestDeviceIdentityNegatives(IsolatedAsyncioTestCase):
    async def test_bad_signature_rejected(self):
        gate = TransportAuth(DEVICE_IDENTITY)
        # A well-formed public key but a signature that won't verify.
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        pub = public_key_to_b64(Ed25519PrivateKey.generate().public_key())
        ws = _ScriptedWS(response={"type": "auth_response", "public_key": pub,
                                   "signature": "AAAA"})
        result = await gate.authenticate(ws)
        self.assertFalse(result.ok)
        self.assertEqual(ws.last_type(), "auth_error")

    async def test_wrong_message_type_rejected(self):
        gate = TransportAuth(DEVICE_IDENTITY)
        ws = _ScriptedWS(response={"type": "audio", "data": "..."})
        result = await gate.authenticate(ws)
        self.assertFalse(result.ok)
        self.assertEqual(ws.last_type(), "auth_error")

    async def test_handshake_timeout_rejected(self):
        gate = TransportAuth(DEVICE_IDENTITY, handshake_timeout=0.01)

        class _Hang:
            def __init__(self):
                self.sent = []

            async def send_json(self, obj):
                self.sent.append(obj)

            async def receive_json(self):
                await asyncio.sleep(10)   # never answers

        ws = _Hang()
        result = await gate.authenticate(ws)
        self.assertFalse(result.ok)
        self.assertIn("timed out", result.reason)


# --- gateway/auth primitives -------------------------------------------------

class TestAuthPrimitives(TestCase):
    def test_verify_signature_roundtrip(self):
        tmp = tempfile.TemporaryDirectory()
        priv, pub = load_or_generate_keypair(Path(tmp.name) / "k.pem")
        nonce = "nonce-abc-123"
        sig = sign_challenge(priv, nonce)
        self.assertTrue(verify_signature(public_key_to_b64(pub), nonce, sig))
        self.assertFalse(verify_signature(public_key_to_b64(pub), "different-nonce", sig))
        self.assertFalse(verify_signature(public_key_to_b64(pub), nonce, "not-base64!!"))
        self.assertFalse(verify_signature("not-a-key", nonce, sig))
        tmp.cleanup()

    def test_load_authorized_keys(self):
        tmp = tempfile.TemporaryDirectory()
        p = Path(tmp.name) / "authorized_keys"
        p.write_text("# header\n\nAAAA  label-one\nBBBB\n", encoding="utf-8")
        self.assertEqual(load_authorized_keys(p), {"AAAA", "BBBB"})
        self.assertEqual(load_authorized_keys(None), set())
        self.assertEqual(load_authorized_keys(Path(tmp.name) / "missing"), set())
        tmp.cleanup()


# --- edge client handshake rejection path ------------------------------------

class TestClientHandshake(IsolatedAsyncioTestCase):
    async def test_client_raises_on_auth_error(self):
        to_client, to_server = asyncio.Queue(), asyncio.Queue()
        client_ws = _ClientWS(to_client, to_server)
        tmp = tempfile.TemporaryDirectory()

        async def fake_server():
            await to_client.put({"type": "auth_challenge", "nonce": "n1"})
            await to_server.get()  # consume the client's auth_response
            await to_client.put({"type": "auth_error", "message": "device not in trust store"})

        with self.assertRaises(AuthError):
            await asyncio.gather(
                perform_client_handshake(client_ws, DEVICE_IDENTITY,
                                         str(Path(tmp.name) / "id.pem")),
                fake_server(),
            )
        tmp.cleanup()

    async def test_client_rejects_unknown_mode(self):
        with self.assertRaises(AuthError):
            await perform_client_handshake(None, "open", "unused")


if __name__ == "__main__":
    unittest.main()
