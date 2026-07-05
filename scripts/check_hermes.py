#!/usr/bin/env python3
"""Operator probe for a Hermes Agent api_server (quickstart §2, spec 005 T103).

Checks, in order:
  1. GET /health                    — server reachable
  2. GET /v1/capabilities           — runs API advertised, auth posture
  3. auth enforcement               — request WITHOUT bearer must be rejected
  4. one trivial round-trip run     — dispatch → SSE events → snapshot agree

Configuration (env or repo .env): HERMES_BASE_URL (e.g. http://your-host:8642/v1),
HERMES_API_KEY, optional HERMES_SESSION_KEY.

Exit code 0 when everything passes, 1 otherwise.

Usage: .venv/bin/python scripts/check_hermes.py [--skip-run]
"""

import argparse
import asyncio
import json
import os
import sys
import time

import httpx

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

TERMINAL_EVENTS = {"run.completed", "run.failed", "run.cancelled"}
RUN_TIMEOUT_S = 120.0

OK = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
WARN = "\033[33m⚠\033[0m"


def say(mark: str, msg: str) -> None:
    print(f"  {mark} {msg}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-run", action="store_true", help="skip the round-trip run check"
    )
    args = parser.parse_args()

    base_url = os.environ.get("HERMES_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("HERMES_API_KEY", "")
    session_key = os.environ.get("HERMES_SESSION_KEY", "voice-satellite-probe")

    if not base_url:
        print(f"{FAIL} HERMES_BASE_URL is not set (env or .env)")
        return 1
    root_url = base_url[: -len("/v1")] if base_url.endswith("/v1") else base_url
    auth = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    failures = 0

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=60.0)) as client:
        # 1. health ----------------------------------------------------------
        print(f"Hermes probe → {base_url}")
        try:
            resp = await client.get(f"{root_url}/health")
            if resp.status_code == 200:
                say(OK, "/health 200 — server reachable")
            else:
                say(FAIL, f"/health returned {resp.status_code}")
                return 1
        except httpx.HTTPError as exc:
            say(FAIL, f"server unreachable: {exc}")
            return 1

        # 2. capabilities ------------------------------------------------------
        caps = None
        try:
            resp = await client.get(f"{base_url}/capabilities", headers=auth)
            if resp.status_code == 401:
                say(FAIL, "/v1/capabilities → 401: HERMES_API_KEY missing or wrong")
                return 1
            resp.raise_for_status()
            caps = resp.json()
            features = caps.get("features", {})
            runs_api = features.get("run_submission") and features.get(
                "run_events_sse"
            )
            if runs_api:
                say(OK, "capabilities: runs API advertised (async dispatch path)")
            elif features.get("chat_completions"):
                say(WARN, "capabilities: NO runs API — adapter will use chat fallback")
            else:
                say(FAIL, "capabilities: neither runs API nor chat_completions")
                failures += 1
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            say(FAIL, f"/v1/capabilities failed: {exc}")
            return 1

        # 3. auth enforcement ---------------------------------------------------
        auth_required = caps.get("auth", {}).get("required", False)
        if not auth_required:
            say(
                WARN,
                "SERVER IS UNAUTHENTICATED (no API_SERVER_KEY configured). "
                "Anyone who can reach this port owns the agent — and its tools "
                "execute on the server host. Set API_SERVER_KEY now unless this "
                "is a throwaway localhost test.",
            )
        else:
            resp = await client.post(f"{base_url}/runs", json={"input": "x"})
            if resp.status_code == 401:
                say(OK, "auth enforced: request without bearer → 401")
            else:
                say(
                    FAIL,
                    f"auth advertised as required but no-bearer request got "
                    f"{resp.status_code}",
                )
                failures += 1
            if not api_key:
                say(FAIL, "server requires auth but HERMES_API_KEY is not set")
                return 1

        # 4. round-trip run ----------------------------------------------------
        if args.skip_run:
            say(WARN, "round-trip run skipped (--skip-run)")
        else:
            t0 = time.monotonic()
            resp = await client.post(
                f"{base_url}/runs",
                json={"input": "Reply with exactly the single word: pong. "
                               "Do not use any tools."},
                headers={**auth, "X-Hermes-Session-Key": session_key},
            )
            if resp.status_code != 202:
                say(FAIL, f"POST /v1/runs → {resp.status_code}: {resp.text[:200]}")
                return 1
            run_id = resp.json().get("run_id", "")
            say(OK, f"dispatched run {run_id} (202)")
            if resp.headers.get("X-Hermes-Session-Key") == session_key:
                say(OK, "X-Hermes-Session-Key accepted (echoed back)")

            terminal = None
            try:
                async with asyncio.timeout(RUN_TIMEOUT_S):
                    async with client.stream(
                        "GET", f"{base_url}/runs/{run_id}/events", headers=auth
                    ) as stream:
                        stream.raise_for_status()
                        async for line in stream.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            event = json.loads(line[len("data:"):].strip())
                            if event.get("event") in TERMINAL_EVENTS:
                                terminal = event
                                break
            except (TimeoutError, httpx.HTTPError) as exc:
                say(FAIL, f"event stream failed: {exc}")
                failures += 1

            if terminal is None:
                say(FAIL, f"no terminal event within {RUN_TIMEOUT_S:.0f}s")
                failures += 1
            elif terminal["event"] == "run.completed":
                elapsed = time.monotonic() - t0
                output = str(terminal.get("output", ""))[:60]
                say(OK, f"run completed in {elapsed:.1f}s — output: {output!r}")
                snap = await client.get(f"{base_url}/runs/{run_id}", headers=auth)
                if snap.status_code == 200 and snap.json().get("status") == "completed":
                    say(OK, "snapshot agrees (GET /v1/runs/{id} → completed)")
                else:
                    say(FAIL, f"snapshot disagrees: {snap.status_code} {snap.text[:120]}")
                    failures += 1
            else:
                say(FAIL, f"run ended {terminal['event']}: {str(terminal)[:200]}")
                failures += 1

    if failures:
        print(f"\n{FAIL} {failures} check(s) failed")
        return 1
    print(f"\n{OK} Hermes server is ready for the voice adapter")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
