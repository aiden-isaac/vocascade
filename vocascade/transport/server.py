"""
vocascade/transport/server.py — Edge↔server transport authentication gate (OQ-3).

Replaces the retired FastAPI server's implicit-open WebSocket with an *explicit*
auth decision (FR-111). The mode is config-gated and validated at construction:

  • ``trust-network``   — the network boundary (e.g. Tailscale) is the security
    perimeter; no per-device check. A *documented* deliberate choice, not an
    accident.
  • ``device-identity`` — Ed25519 challenge-response: the server presents a
    nonce, the edge signs it with its private key, the server verifies the
    signature and (when a trust store is configured) checks the device's public
    key against the allowlist.

Anything else raises at construction — the server refuses to start rather than
silently fall back to an open endpoint.

The gate talks to any object exposing async ``send_json`` / ``receive_json``
(a FastAPI ``WebSocket``, or a test double).
"""

import asyncio
import base64
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from vocascade.gateway.auth import verify_signature, load_authorized_keys

logger = logging.getLogger("vocascade.transport.server")

TRUST_NETWORK = "trust-network"
DEVICE_IDENTITY = "device-identity"
VALID_MODES = (TRUST_NETWORK, DEVICE_IDENTITY)


@dataclass(frozen=True)
class AuthResult:
    """Outcome of a transport handshake."""
    ok: bool
    reason: str
    device_id: str | None = None   # base64 public key of an authenticated device


class TransportAuth:
    """
    The transport-auth gate. Constructed once (in ``lifespan``) from config;
    construction fails fast on an unknown/missing mode so the endpoint can never
    default to unauthenticated.
    """

    def __init__(self, mode: str, authorized_keys_path: str | Path | None = None,
                 handshake_timeout: float = 5.0):
        if mode not in VALID_MODES:
            raise ValueError(
                f"transport_auth_mode must be one of {VALID_MODES!r}, got {mode!r}. "
                "The transport refuses to serve without an explicit auth decision (FR-111)."
            )
        self.mode = mode
        self.authorized_keys_path = authorized_keys_path
        self.handshake_timeout = handshake_timeout
        if mode == DEVICE_IDENTITY:
            allowlist = load_authorized_keys(authorized_keys_path)
            if allowlist:
                logger.info("Transport auth: device-identity, %d authorized device(s)", len(allowlist))
            else:
                logger.warning(
                    "Transport auth: device-identity with NO trust store configured "
                    "(%s). Any device presenting a valid signature is accepted and its "
                    "fingerprint logged for pinning. Set AUTHORIZED_KEYS_PATH to enforce "
                    "an allowlist.", authorized_keys_path or "unset")
        else:
            logger.info("Transport auth: trust-network (network boundary is the perimeter)")

    async def authenticate(self, ws) -> AuthResult:
        """Run the configured handshake. Sends ``auth_ok``/``auth_error`` to the client."""
        if self.mode == TRUST_NETWORK:
            return AuthResult(ok=True, reason="trust-network boundary")
        return await self._authenticate_device(ws)

    async def _authenticate_device(self, ws) -> AuthResult:
        nonce = base64.b64encode(os.urandom(32)).decode("utf-8")
        try:
            await ws.send_json({"type": "auth_challenge", "nonce": nonce})
            resp = await asyncio.wait_for(ws.receive_json(), timeout=self.handshake_timeout)
        except asyncio.TimeoutError:
            return await self._reject(ws, "handshake timed out")
        except Exception as e:
            return await self._reject(ws, f"handshake transport error: {e}")

        if not isinstance(resp, dict) or resp.get("type") != "auth_response":
            return await self._reject(ws, "expected an auth_response message")
        public_key = resp.get("public_key")
        signature = resp.get("signature")
        if not public_key or not signature:
            return await self._reject(ws, "auth_response missing public_key or signature")

        if not verify_signature(public_key, nonce, signature):
            return await self._reject(ws, "invalid device signature")

        allowlist = load_authorized_keys(self.authorized_keys_path)
        if allowlist and public_key not in allowlist:
            return await self._reject(ws, "device not in trust store")
        if not allowlist:
            # Trust-on-first-use: identity proven, not yet pinned. Surface it.
            logger.warning("Accepted unpinned device identity %s…", public_key[:16])

        try:
            await ws.send_json({"type": "auth_ok"})
        except Exception:
            pass  # client may have raced a close; the verified result still stands
        logger.info("Device authenticated: %s…", public_key[:16])
        return AuthResult(ok=True, reason="device-identity verified", device_id=public_key)

    async def _reject(self, ws, reason: str) -> AuthResult:
        logger.warning("Transport auth rejected: %s", reason)
        try:
            await ws.send_json({"type": "auth_error", "message": reason})
        except Exception:
            pass
        return AuthResult(ok=False, reason=reason)


def transport_auth_from_config(config) -> TransportAuth:
    """Build the gate from an ``AdapterConfig``; raises on an invalid auth mode."""
    return TransportAuth(
        mode=config.transport_auth_mode,
        authorized_keys_path=getattr(config, "authorized_keys_path", None),
    )
