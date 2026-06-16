"""Unit tests for vocascade.hermes_run_client (spec 005 T108).

All HTTP is mocked with httpx.MockTransport — no network.
"""

import json
import unittest

import httpx

from vocascade.hermes_run_client import (
    Capabilities,
    HermesRunClient,
    RunDispatchError,
    RunEventKind,
)

BASE = "http://hermes-test:8642/v1"


def sse(events: list[dict | str]) -> bytes:
    """Render a list of event dicts (or raw comment strings) as an SSE body."""
    out = []
    for e in events:
        if isinstance(e, str):
            out.append(e)  # raw line, e.g. ": keepalive"
        else:
            out.append(f"data: {json.dumps(e)}")
        out.append("")  # blank separator line
    return ("\n".join(out) + "\n").encode()


def make_client(handler, **kwargs) -> HermesRunClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return HermesRunClient(
        base_url=BASE,
        api_key="test-key",
        session_key="voice-satellite",
        http_client=http_client,
        initial_backoff=0.0,
        max_backoff=0.0,
        **kwargs,
    )


CAPS_FULL = {
    "object": "hermes.api_server.capabilities",
    "model": "hermes-agent",
    "auth": {"type": "bearer", "required": True},
    "features": {
        "chat_completions": True,
        "run_submission": True,
        "run_status": True,
        "run_events_sse": True,
        "run_stop": True,
    },
}


class TestHeadersAndUrl(unittest.IsolatedAsyncioTestCase):
    async def test_auth_and_session_headers_present_on_dispatch(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["headers"] = request.headers
            seen["url"] = str(request.url)
            return httpx.Response(202, json={"run_id": "run_1", "status": "started"})

        client = make_client(handler)
        await client.start_run("do something", session_id="sess-42")
        self.assertEqual(seen["url"], f"{BASE}/runs")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(seen["headers"]["X-Hermes-Session-Key"], "voice-satellite")
        self.assertEqual(seen["headers"]["X-Hermes-Session-Id"], "sess-42")

    async def test_base_url_gets_v1_appended(self):
        client = HermesRunClient(base_url="http://hermes-test:8642")
        self.assertEqual(client.base_url, "http://hermes-test:8642/v1")


class TestDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_202_dispatch_returns_handle(self):
        def handler(request):
            body = json.loads(request.content)
            assert body["input"] == "check my email"
            assert body["model"] == "hermes-agent"
            return httpx.Response(202, json={"run_id": "run_abc", "status": "started"})

        client = make_client(handler)
        handle = await client.start_run("check my email")
        self.assertEqual(handle.run_id, "run_abc")
        self.assertEqual(handle.status, "started")

    async def test_instructions_attached_when_set(self):
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            return httpx.Response(202, json={"run_id": "run_x", "status": "started"})

        client = make_client(handler, instructions="speak briefly")
        await client.start_run("hi")
        self.assertEqual(seen["body"]["instructions"], "speak briefly")

    async def test_instructions_omitted_when_empty(self):
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            return httpx.Response(202, json={"run_id": "run_x", "status": "started"})

        client = make_client(handler)  # default instructions=""
        await client.start_run("hi")
        self.assertNotIn("instructions", seen["body"])

    async def test_non_202_raises_dispatch_error(self):
        def handler(request):
            return httpx.Response(429, json={"error": {"message": "too many runs"}})

        client = make_client(handler)
        with self.assertRaises(RunDispatchError):
            await client.start_run("x")

    async def test_network_error_raises_dispatch_error(self):
        def handler(request):
            raise httpx.ConnectError("boom")

        client = make_client(handler)
        with self.assertRaises(RunDispatchError):
            await client.start_run("x")


class TestCapabilitiesProbe(unittest.IsolatedAsyncioTestCase):
    async def test_probe_parses_runs_support(self):
        def handler(request):
            return httpx.Response(200, json=CAPS_FULL)

        client = make_client(handler)
        caps = await client.probe_capabilities()
        self.assertTrue(caps.supports_runs)
        self.assertTrue(caps.auth_required)
        self.assertTrue(caps.supports_chat)
        self.assertEqual(caps.model_name, "hermes-agent")

    async def test_probe_failure_sets_fallback_flag_and_reprobes(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(503)
            return httpx.Response(200, json=CAPS_FULL)

        client = make_client(handler)
        caps = await client.probe_capabilities()
        self.assertFalse(caps.supports_runs)  # failure => fallback flag
        # Failure is NOT cached: the next probe hits the server again.
        caps2 = await client.probe_capabilities()
        self.assertTrue(caps2.supports_runs)
        # Success IS cached.
        caps3 = await client.probe_capabilities()
        self.assertTrue(caps3.supports_runs)
        self.assertEqual(calls["n"], 2)

    async def test_probe_missing_runs_features(self):
        def handler(request):
            return httpx.Response(
                200,
                json={"model": "x", "features": {"chat_completions": True}},
            )

        client = make_client(handler)
        caps = await client.probe_capabilities()
        self.assertFalse(caps.supports_runs)
        self.assertTrue(caps.supports_chat)


class TestEventStream(unittest.IsolatedAsyncioTestCase):
    async def test_parses_events_with_unknown_kinds_and_keepalives(self):
        body = sse([
            ": keepalive",
            {"event": "run.started", "run_id": "run_1", "user_message": {"role": "user"}},
            {"event": "message.delta", "run_id": "run_1", "delta": "po"},
            {"event": "totally.new.event", "run_id": "run_1"},
            ": keepalive",
            {"event": "run.completed", "run_id": "run_1", "output": "pong",
             "usage": {"total_tokens": 3}},
            ": stream closed",
        ])

        def handler(request):
            return httpx.Response(200, content=body,
                                  headers={"Content-Type": "text/event-stream"})

        client = make_client(handler)
        events = [e async for e in client.stream_events("run_1")]
        kinds = [e.kind for e in events]
        self.assertEqual(kinds, [
            RunEventKind.ACCEPTED,
            RunEventKind.PROGRESS,
            RunEventKind.UNKNOWN,
            RunEventKind.COMPLETED,
        ])
        self.assertEqual(events[-1].result_text, "pong")
        self.assertTrue(events[-1].is_terminal)
        # raw line preserved for contract-drift debugging
        self.assertIn("totally.new.event", events[2].raw)

    async def test_stream_drop_reconciles_via_snapshot(self):
        """Stream dies mid-run; reconnect 404s (server queue destroyed);
        get_run eventually reports terminal => synthesized COMPLETED event."""
        calls = {"events": 0, "get": 0}

        def handler(request):
            path = request.url.path
            if path.endswith("/events"):
                calls["events"] += 1
                if calls["events"] == 1:
                    # one progress event, then the stream drops (no terminal)
                    return httpx.Response(200, content=sse([
                        {"event": "message.delta", "run_id": "run_1", "delta": "x"},
                    ]))
                return httpx.Response(404, json={"error": {"message": "run_not_found"}})
            if path.endswith("/runs/run_1"):
                calls["get"] += 1
                if calls["get"] == 1:
                    return httpx.Response(200, json={"run_id": "run_1", "status": "running"})
                return httpx.Response(200, json={
                    "run_id": "run_1", "status": "completed", "output": "all done",
                })
            raise AssertionError(f"unexpected path {path}")

        client = make_client(handler)
        events = [e async for e in client.stream_events("run_1")]
        self.assertEqual(events[0].kind, RunEventKind.PROGRESS)
        self.assertEqual(events[-1].kind, RunEventKind.COMPLETED)
        self.assertEqual(events[-1].result_text, "all done")
        self.assertGreaterEqual(calls["get"], 2)

    async def test_stream_drop_failed_run_synthesizes_failed(self):
        def handler(request):
            path = request.url.path
            if path.endswith("/events"):
                return httpx.Response(404)
            return httpx.Response(200, json={
                "run_id": "run_1", "status": "failed", "error": "tool exploded",
            })

        client = make_client(handler)
        events = [e async for e in client.stream_events("run_1")]
        self.assertEqual([e.kind for e in events], [RunEventKind.FAILED])


class TestGetRunAndControl(unittest.IsolatedAsyncioTestCase):
    async def test_get_run_reconciliation_fields(self):
        def handler(request):
            return httpx.Response(200, json={
                "object": "hermes.run",
                "run_id": "run_9",
                "status": "completed",
                "output": "result text",
                "last_event": "run.completed",
            })

        client = make_client(handler)
        state = await client.get_run("run_9")
        self.assertEqual(state.status, "completed")
        self.assertEqual(state.result_text, "result text")
        self.assertTrue(state.is_terminal)

    async def test_stop_run(self):
        def handler(request):
            assert request.url.path.endswith("/runs/run_9/stop")
            return httpx.Response(200, json={"run_id": "run_9", "status": "stopping"})

        client = make_client(handler)
        out = await client.stop_run("run_9")
        self.assertEqual(out["status"], "stopping")

    async def test_resolve_approval_maps_bool_to_choice(self):
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "object": "hermes.run.approval_response",
                "run_id": "run_9", "choice": seen["body"]["choice"], "resolved": 1,
            })

        client = make_client(handler)
        await client.resolve_approval("run_9", True)
        self.assertEqual(seen["body"], {"choice": "once"})
        await client.resolve_approval("run_9", False)
        self.assertEqual(seen["body"], {"choice": "deny"})
        await client.resolve_approval("run_9", "always")
        self.assertEqual(seen["body"], {"choice": "always"})


if __name__ == "__main__":
    unittest.main()
