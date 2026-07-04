"""Live contract test for the Hermes Agent api_server (spec 005, T101).

Pins the subset of the Hermes API consumed by the voice adapter, as
documented in docs/legacy-specs/005-hermes-agent-backend/contracts/hermes-api.md.

The whole module is SKIPPED unless a Hermes server is reachable at
HERMES_BASE_URL (read from the environment, falling back to the repo .env).
It dispatches two real (trivial) runs, so expect ~30-60 s when live.

Run: PYTHONPATH=. .venv/bin/python -m pytest tests/contract/ -v
"""

import asyncio
import json
import os
import unittest
import uuid

import httpx

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

BASE_URL = os.environ.get("HERMES_BASE_URL", "").rstrip("/")
API_KEY = os.environ.get("HERMES_API_KEY", "")
# Base URL carries /v1; /health lives at the server root.
ROOT_URL = BASE_URL[: -len("/v1")] if BASE_URL.endswith("/v1") else BASE_URL

TERMINAL_EVENTS = {"run.completed", "run.failed", "run.cancelled"}
# Trivial prompts still pay full agent-context ingestion (~20k tokens observed).
RUN_TIMEOUT_S = 120.0

NO_TOOLS = " Do not use any tools."


def _server_reachable() -> bool:
    if not BASE_URL:
        return False
    try:
        resp = httpx.get(f"{ROOT_URL}/health", timeout=3.0)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


SERVER_UP = _server_reachable()


def _auth_headers() -> dict:
    headers = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    return headers


async def _collect_events(client: httpx.AsyncClient, run_id: str) -> list[dict]:
    """Consume the per-run SSE stream until the server closes it.

    Events are `data: <json>` lines with an `event` discriminator;
    comment lines (`: keepalive`, `: stream closed`) are ignored.
    """
    events = []
    async with client.stream(
        "GET", f"{BASE_URL}/runs/{run_id}/events", headers=_auth_headers()
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            events.append(json.loads(line[len("data:"):].strip()))
            if events[-1].get("event") in TERMINAL_EVENTS:
                break
    return events


@unittest.skipUnless(
    SERVER_UP, f"no Hermes server reachable at HERMES_BASE_URL={BASE_URL!r}"
)
class TestHermesApiContract(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=60.0))

    async def asyncTearDown(self):
        await self.client.aclose()

    async def _dispatch(self, text: str, extra_headers: dict | None = None) -> dict:
        headers = {**_auth_headers(), **(extra_headers or {})}
        resp = await self.client.post(
            f"{BASE_URL}/runs",
            json={"input": text, "model": "hermes-agent"},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 202, resp.text)
        body = resp.json()
        self.assertIn("run_id", body)
        self.assertIn("status", body)
        return body

    # --- health & capabilities -------------------------------------------

    async def test_health_returns_200(self):
        resp = await self.client.get(f"{ROOT_URL}/health")
        self.assertEqual(resp.status_code, 200)

    async def test_capabilities_advertise_runs_api(self):
        resp = await self.client.get(
            f"{BASE_URL}/capabilities", headers=_auth_headers()
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        caps = resp.json()
        self.assertEqual(caps["object"], "hermes.api_server.capabilities")
        features = caps["features"]
        # The adapter's probe keys: runs API present => async dispatch path.
        for flag in ("run_submission", "run_status", "run_events_sse", "run_stop"):
            self.assertTrue(features.get(flag), f"features.{flag} not advertised")
        self.assertTrue(features.get("chat_completions"), "fallback path missing")
        self.assertEqual(features["session_key_header"], "X-Hermes-Session-Key")
        self.assertEqual(features["session_continuity_header"], "X-Hermes-Session-Id")

    # --- auth --------------------------------------------------------------

    async def test_auth_required_without_bearer(self):
        caps = await self.client.get(
            f"{BASE_URL}/capabilities", headers=_auth_headers()
        )
        if not caps.json().get("auth", {}).get("required", False):
            self.skipTest("server runs unauthenticated (test-only mode)")
        resp = await self.client.post(f"{BASE_URL}/runs", json={"input": "x"})
        self.assertEqual(resp.status_code, 401)
        resp = await self.client.get(f"{BASE_URL}/capabilities")
        self.assertEqual(resp.status_code, 401, "capabilities also requires auth")

    # --- run lifecycle -------------------------------------------------------

    async def test_run_dispatch_stream_and_snapshot_agree(self):
        session_id = str(uuid.uuid4())
        body = await self._dispatch(
            "Reply with exactly the single word: pong." + NO_TOOLS,
            extra_headers={
                "X-Hermes-Session-Key": "voice-contract-test",
                "X-Hermes-Session-Id": session_id,
            },
        )
        run_id = body["run_id"]
        self.assertTrue(run_id.startswith("run_"))
        self.assertIn(body["status"], {"started", "queued", "running"})

        events = await asyncio.wait_for(
            _collect_events(self.client, run_id), timeout=RUN_TIMEOUT_S
        )
        self.assertTrue(events, "no events received")
        kinds = [e["event"] for e in events]
        terminal = events[-1]
        self.assertEqual(terminal["event"], "run.completed", f"events: {kinds}")
        self.assertIn("output", terminal)
        self.assertIn("usage", terminal)
        
        # Verify message.delta streaming contract (OQ-1 / T201)
        delta_events = [e for e in events if e.get("event") == "message.delta"]
        self.assertTrue(
            len(delta_events) >= 1,
            f"Expected at least one message.delta event, but got events: {kinds}"
        )
        non_empty_deltas = [de for de in delta_events if de.get("delta")]
        self.assertTrue(
            len(non_empty_deltas) >= 1,
            "Expected at least one message.delta event with non-empty 'delta'"
        )
        
        for e in events:
            self.assertEqual(e["run_id"], run_id)
            self.assertIn("timestamp", e)

        # Snapshot must agree with the stream's terminal event.
        resp = await self.client.get(
            f"{BASE_URL}/runs/{run_id}", headers=_auth_headers()
        )
        self.assertEqual(resp.status_code, 200)
        snap = resp.json()
        self.assertEqual(snap["object"], "hermes.run")
        self.assertEqual(snap["run_id"], run_id)
        self.assertEqual(snap["status"], "completed")
        self.assertEqual(snap["last_event"], "run.completed")
        self.assertEqual(snap["output"], terminal["output"])

    async def test_stop_cancels_run(self):
        body = await self._dispatch(
            "Write a 500 word essay about clouds." + NO_TOOLS
        )
        run_id = body["run_id"]
        collector = asyncio.create_task(_collect_events(self.client, run_id))
        await asyncio.sleep(1.0)

        resp = await self.client.post(
            f"{BASE_URL}/runs/{run_id}/stop", headers=_auth_headers()
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "stopping")

        events = await asyncio.wait_for(collector, timeout=30.0)
        self.assertEqual(events[-1]["event"], "run.cancelled")

        resp = await self.client.get(
            f"{BASE_URL}/runs/{run_id}", headers=_auth_headers()
        )
        snap = resp.json()
        self.assertEqual(snap["status"], "cancelled")
        self.assertEqual(snap["last_event"], "run.cancelled")

    async def test_unknown_run_returns_404(self):
        resp = await self.client.get(
            f"{BASE_URL}/runs/run_{uuid.uuid4().hex}", headers=_auth_headers()
        )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
