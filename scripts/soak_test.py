#!/usr/bin/env python3
"""
scripts/soak_test.py — SC-011 growth check for the vocascade broker + delivery.

Drives a long stream of Hermes dispatches (each completed) through the real
TaskBroker + DeliveryCoordinator, periodically reporting the task registry /
delivery queue sizes and process RSS, and verifies the task registry stays
bounded (terminal tasks are pruned) rather than growing for the life of the
process.

  Smoke:  PYTHONPATH=. .venv/bin/python scripts/soak_test.py --iterations 5000
  24h:    PYTHONPATH=. .venv/bin/python scripts/soak_test.py --duration 86400

Exits non-zero if the task registry exceeds its bound (a leak).
"""
import argparse
import asyncio
import os
import time

from vocascade.task_broker import TaskBroker
from vocascade.delivery import DeliveryCoordinator
from vocascade.hermes_run_client import RunEvent, RunEventKind, RunHandle, Capabilities


class _CompletingClient:
    """A fake run client that accepts every run and completes it immediately."""

    def __init__(self):
        self._n = 0

    async def probe_capabilities(self, force=False):
        return Capabilities(supports_runs=True)

    async def start_run(self, prompt, *, session_id=""):
        self._n += 1
        return RunHandle(run_id=f"run_{self._n}", status="running")

    async def stream_events(self, run_id, *, session_id=""):
        yield RunEvent(run_id=run_id, kind=RunEventKind.COMPLETED,
                       payload={"event": "run.completed", "output": "ok"})

    async def aclose(self):
        pass


def _rss_mb() -> int:
    try:
        with open(f"/proc/{os.getpid()}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return -1


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=0, help="stop after N dispatches")
    ap.add_argument("--duration", type=float, default=0.0, help="stop after S seconds")
    ap.add_argument("--report-every", type=int, default=500)
    args = ap.parse_args()
    if not args.iterations and not args.duration:
        args.iterations = 5000

    delivery = DeliveryCoordinator()
    broker = TaskBroker(_CompletingClient(), delivery)

    start = time.time()
    i = 0
    peak_tasks = 0
    while True:
        i += 1
        await broker.dispatch(f"soak query {i}", session_id="soak")
        for _ in range(3):           # let the consumer drain the completion event
            await asyncio.sleep(0)
        peak_tasks = max(peak_tasks, len(broker.tasks))

        if i % args.report_every == 0:
            print(f"iter={i} tasks={len(broker.tasks)} (peak {peak_tasks}) "
                  f"queue={len(delivery.queue)} consumers={len(broker._consumers)} "
                  f"rss={_rss_mb()}MB elapsed={time.time()-start:.0f}s", flush=True)

        if args.iterations and i >= args.iterations:
            break
        if args.duration and (time.time() - start) >= args.duration:
            break

    await broker.shutdown()
    bound = broker._MAX_TERMINAL + 50          # cap + a little slack for in-flight
    ok = len(broker.tasks) <= bound
    print(f"\nDONE iters={i} final_tasks={len(broker.tasks)} peak={peak_tasks} "
          f"bound={broker._MAX_TERMINAL} -> {'PASS' if ok else 'FAIL — registry unbounded!'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
