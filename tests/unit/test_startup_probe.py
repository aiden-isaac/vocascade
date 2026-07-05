"""
tests/unit/test_startup_probe.py — startup health-report endpoint probes (D5).

The probe maps classified LLM failures to human verdicts (OK / AUTH REJECTED /
UNREACHABLE) and never raises — a dead endpoint must not block startup.
"""

import unittest
from unittest.mock import patch

import httpx

from vocascade.__main__ import probe_llm, probe_hermes


class _Cfg:
    llm_base_url = "http://test/v1"
    llm_api_key = None
    llm_model = "m"
    hermes_base_url = ""
    hermes_api_key = None


def _with_transport(handler):
    """Patch local_llm's httpx.AsyncClient to answer via MockTransport."""
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    return patch("vocascade.gateway.local_llm.httpx.AsyncClient",
                 lambda *a, **kw: real_client(transport=transport))


class TestProbeLLM(unittest.TestCase):
    def test_ok(self):
        handler = lambda req: httpx.Response(
            200, json={"choices": [{"message": {"content": "pong"}}]})
        with _with_transport(handler):
            self.assertEqual(probe_llm(_Cfg()), "OK")

    def test_auth_rejected(self):
        with _with_transport(lambda req: httpx.Response(401)):
            self.assertIn("AUTH REJECTED", probe_llm(_Cfg()))

    def test_unreachable(self):
        def boom(req):
            raise httpx.ConnectError("connection refused")

        with _with_transport(boom):
            self.assertIn("UNREACHABLE", probe_llm(_Cfg()))


class TestProbeHermes(unittest.TestCase):
    def test_not_configured_is_local_only(self):
        self.assertIn("local-only", probe_hermes(_Cfg()))


if __name__ == "__main__":
    unittest.main()
