"""
HermesRunClient — the only component that speaks HTTP to the Hermes Agent
api_server (spec 005). Wraps the async runs API pinned in
specs/005-hermes-agent-backend/contracts/hermes-api.md:

    POST /v1/runs                    dispatch, 202 + run_id
    GET  /v1/runs/{id}               snapshot (reconciliation)
    GET  /v1/runs/{id}/events        per-run SSE lifecycle stream
    POST /v1/runs/{id}/stop          cancel
    POST /v1/runs/{id}/approval      resolve pending approval
    GET  /v1/capabilities            feature probe (requires auth)

Contract subtlety that shapes this client: the server keeps ONE in-memory
event queue per run and destroys it when a subscriber disconnects, so a
reconnect typically 404s. "Reconnect with backoff" therefore means: on any
stream drop, reconcile via get_run() and synthesize the terminal event from
the snapshot once the run is terminal.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Optional

import httpx

logger = logging.getLogger("voice_adapter.hermes_run_client")


class RunEventKind(str, Enum):
    ACCEPTED = "accepted"
    PROGRESS = "progress"
    APPROVAL_REQUIRED = "approval_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


TERMINAL_KINDS = frozenset(
    {RunEventKind.COMPLETED, RunEventKind.FAILED, RunEventKind.CANCELLED}
)

# Upstream event names pinned in contracts/hermes-api.md (T102).
_EVENT_KIND_MAP = {
    "run.started": RunEventKind.ACCEPTED,
    "message.delta": RunEventKind.PROGRESS,
    "reasoning.available": RunEventKind.PROGRESS,
    "tool.started": RunEventKind.PROGRESS,
    "tool.completed": RunEventKind.PROGRESS,
    "tool.failed": RunEventKind.PROGRESS,
    "approval.request": RunEventKind.APPROVAL_REQUIRED,
    "approval.responded": RunEventKind.PROGRESS,
    "run.completed": RunEventKind.COMPLETED,
    "run.failed": RunEventKind.FAILED,
    "run.cancelled": RunEventKind.CANCELLED,
}

# Snapshot status vocabulary (terminal subset) pinned in T102.
_TERMINAL_STATUS_TO_KIND = {
    "completed": RunEventKind.COMPLETED,
    "failed": RunEventKind.FAILED,
    "cancelled": RunEventKind.CANCELLED,
}


@dataclass
class RunEvent:
    run_id: str
    kind: RunEventKind
    payload: dict = field(default_factory=dict)
    raw: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.kind in TERMINAL_KINDS

    @property
    def result_text(self) -> Optional[str]:
        """Result text for COMPLETED events (contract: `output` field)."""
        out = self.payload.get("output")
        return str(out) if out is not None else None


@dataclass
class RunHandle:
    run_id: str
    status: str


@dataclass
class RunState:
    run_id: str
    status: str
    result_text: Optional[str] = None
    raw: dict = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUS_TO_KIND


@dataclass
class Capabilities:
    supports_runs: bool
    model_name: str = "hermes-agent"
    auth_required: bool = True
    supports_chat: bool = True
    raw: dict = field(default_factory=dict)


class RunDispatchError(Exception):
    """POST /v1/runs failed (network, auth, 4xx/5xx)."""


class HermesRunClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        session_key: str | None = None,
        model: str = "hermes-agent",
        http_client: httpx.AsyncClient | None = None,
        initial_backoff: float = 1.0,
        max_backoff: float = 60.0,
    ) -> None:
        # Contract: the base URL carries /v1.
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/v1"):
            logger.warning(
                "HERMES_BASE_URL %r does not end with /v1 — appending it", base_url
            )
            self.base_url = f"{self.base_url}/v1"
        self.api_key = api_key
        self.session_key = session_key
        self.model = model
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self._client = http_client
        self._owns_client = http_client is None
        self._capabilities: Capabilities | None = None
        self._chat_client = None  # lazy HermesClient for the fallback path

    # ── plumbing ─────────────────────────────────────────────────────────────

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=90.0))
        return self._client

    def headers(self, session_id: str | None = None) -> dict:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.session_key:
            headers["X-Hermes-Session-Key"] = self.session_key
        if session_id:
            headers["X-Hermes-Session-Id"] = session_id
        return headers

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
        if self._chat_client is not None:
            await self._chat_client.close()
            self._chat_client = None

    # ── capabilities ─────────────────────────────────────────────────────────

    async def probe_capabilities(self, force: bool = False) -> Capabilities:
        """Probe /v1/capabilities. Failure ⇒ supports_runs=False, NOT cached
        (lazy re-probe on the next call so a transient outage doesn't pin us
        to fallback mode forever)."""
        if self._capabilities is not None and not force:
            return self._capabilities
        try:
            resp = await self.client.get(
                f"{self.base_url}/capabilities", headers=self.headers()
            )
            resp.raise_for_status()
            raw = resp.json()
            features = raw.get("features", {})
            caps = Capabilities(
                supports_runs=bool(
                    features.get("run_submission") and features.get("run_events_sse")
                ),
                model_name=raw.get("model", self.model),
                auth_required=bool(raw.get("auth", {}).get("required", False)),
                supports_chat=bool(features.get("chat_completions")),
                raw=raw,
            )
            self._capabilities = caps
            return caps
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            logger.warning("Capabilities probe failed (%s) — fallback mode", exc)
            return Capabilities(supports_runs=False, raw={})

    # ── runs API ─────────────────────────────────────────────────────────────

    async def start_run(self, prompt: str, *, session_id: str = "") -> RunHandle:
        try:
            resp = await self.client.post(
                f"{self.base_url}/runs",
                json={"input": prompt, "model": self.model},
                headers=self.headers(session_id),
            )
        except httpx.HTTPError as exc:
            raise RunDispatchError(f"dispatch failed: {exc}") from exc
        if resp.status_code != 202:
            raise RunDispatchError(
                f"dispatch failed: HTTP {resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        return RunHandle(run_id=body["run_id"], status=body.get("status", "started"))

    async def get_run(self, run_id: str) -> RunState:
        resp = await self.client.get(
            f"{self.base_url}/runs/{run_id}", headers=self.headers()
        )
        resp.raise_for_status()
        raw = resp.json()
        return RunState(
            run_id=run_id,
            status=raw.get("status", ""),
            result_text=raw.get("output"),
            raw=raw,
        )

    async def stop_run(self, run_id: str) -> dict:
        resp = await self.client.post(
            f"{self.base_url}/runs/{run_id}/stop", headers=self.headers()
        )
        resp.raise_for_status()
        return resp.json()

    async def resolve_approval(self, run_id: str, decision: bool | str) -> dict:
        """Resolve a pending approval. Voice mapping: True→once, False→deny;
        a string is passed through (once|session|always|deny)."""
        if isinstance(decision, bool):
            choice = "once" if decision else "deny"
        else:
            choice = decision
        resp = await self.client.post(
            f"{self.base_url}/runs/{run_id}/approval",
            json={"choice": choice},
            headers=self.headers(),
        )
        resp.raise_for_status()
        return resp.json()

    # ── event stream ─────────────────────────────────────────────────────────

    def _parse_event(self, data_line: str) -> RunEvent | None:
        data_str = data_line.removeprefix("data:").strip()
        if not data_str:
            return None
        try:
            payload = json.loads(data_str)
        except json.JSONDecodeError:
            logger.warning("Unparseable SSE data line: %.200s", data_str)
            return None
        name = payload.get("event", "")
        kind = _EVENT_KIND_MAP.get(name)
        if kind is None:
            logger.info("Unknown run event %r — skipping (never fatal)", name)
            kind = RunEventKind.UNKNOWN
        return RunEvent(
            run_id=payload.get("run_id", ""),
            kind=kind,
            payload=payload,
            raw=data_str,
        )

    @staticmethod
    def _event_from_state(state: RunState) -> RunEvent:
        """Synthesize the terminal event from a snapshot (used after stream
        gaps, when the server-side event queue is already gone)."""
        kind = _TERMINAL_STATUS_TO_KIND[state.status]
        payload = {"event": f"run.{state.status}", "run_id": state.run_id}
        if state.result_text is not None:
            payload["output"] = state.result_text
        return RunEvent(run_id=state.run_id, kind=kind, payload=payload, raw="")

    async def stream_events(
        self, run_id: str, *, session_id: str = ""
    ) -> AsyncIterator[RunEvent]:
        """Yield RunEvents until a terminal event. Keepalive comments are
        swallowed. On stream drop or failed (re)connect, reconcile via
        get_run(); terminal snapshot ⇒ synthesized terminal event. Backoff
        grows initial_backoff → max_backoff between attempts."""
        backoff = self.initial_backoff
        while True:
            dropped_exc: Exception | None = None
            try:
                async with self.client.stream(
                    "GET",
                    f"{self.base_url}/runs/{run_id}/events",
                    headers=self.headers(session_id),
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line or line.startswith(":"):
                            continue  # keepalive / close comments
                        if not line.startswith("data:"):
                            continue
                        event = self._parse_event(line)
                        if event is None:
                            continue
                        backoff = self.initial_backoff  # healthy stream
                        yield event
                        if event.is_terminal:
                            return
                # Stream closed without a terminal event — fall through to
                # reconciliation below.
            except httpx.HTTPError as exc:
                dropped_exc = exc

            try:
                state = await self.get_run(run_id)
            except httpx.HTTPError as exc:
                logger.warning(
                    "Run %s: stream dropped (%s) and snapshot failed (%s); "
                    "retrying in %.1fs",
                    run_id, dropped_exc, exc, backoff,
                )
            else:
                if state.is_terminal:
                    logger.info(
                        "Run %s: terminal state %r recovered via snapshot",
                        run_id, state.status,
                    )
                    yield self._event_from_state(state)
                    return
                logger.info(
                    "Run %s: stream dropped (%s), still %r — reconnecting in %.1fs",
                    run_id, dropped_exc, state.status, backoff,
                )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.max_backoff)

    # ── chat fallback ────────────────────────────────────────────────────────

    async def chat_fallback(
        self, prompt: str, *, session_id: str = ""
    ) -> AsyncIterator[str]:
        """Streaming chat-completions fallback when the runs API is absent.
        Delegates to the existing HermesClient streaming parser."""
        from voice_satellite.gateway.hermes_client import HermesClient

        if self._chat_client is None:
            self._chat_client = HermesClient(
                base_url=self.base_url,
                api_key=self.api_key,
                session_key=self.session_key,
            )
            await self._chat_client.connect()
            if session_id:
                self._chat_client.session_id = session_id
        async for chunk in self._chat_client.send_transcript(prompt):
            yield chunk
