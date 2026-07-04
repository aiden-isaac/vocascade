# Quickstart: Hermes Agent Backend

**Branch**: `005-hermes-agent-backend` | **Spec**: [spec.md](spec.md)

Two supported topologies, same configuration surface. Pick one:

- **A. Co-located** — Hermes Agent runs on the same machine as the voice
  adapter. Fresh-machine friendly; zero external services.
- **B. Remote** — Hermes Agent runs on another Tailscale host (reference:
  `jarlaxle`); the adapter machine reaches it over HTTP and reads its memory
  files over SSH.

## 1. Install Hermes Agent (on the Hermes host)

```bash
# Official installer (handles uv, Python 3.11, Node, ripgrep, ffmpeg)
curl -fsSL https://hermes.nousresearch.com/install.sh | bash   # see upstream README for the current URL

hermes            # first-run setup: pick a model provider (local endpoint works)
```

Memory works out of the box — **no external services**: Hermes maintains
`~/.hermes/MEMORY.md`, `~/.hermes/USER.md`, and an SQLite session DB with
full-text search. They survive restarts; that's the persistence default.

> **Optional — Honcho**: if you run a Honcho server, opt in *on the Hermes
> side*: `hermes memory setup honcho` (sets `memory.provider: honcho` in
> `~/.hermes/config.yaml`). Nothing in the voice stack changes.

> **Profiles**: if you use Hermes profiles, every `~/.hermes` path below refers
> to the active profile's home (`get_hermes_home()`).

## 2. Enable the API server adapter

In the Hermes host's environment (e.g. `~/.hermes/.env`):

```bash
API_SERVER_KEY=<generate a long random secret>   # REQUIRED — without it the API is UNAUTHENTICATED
API_SERVER_HOST=127.0.0.1                        # topology A
# API_SERVER_HOST=<tailscale-ip-of-host>         # topology B (bind Tailscale, NEVER a public interface)
API_SERVER_PORT=8642
```

Enable the api_server platform in the gateway config, then start it:

```bash
hermes gateway        # run under systemd/tmux for persistence
```

Validate from the adapter machine:

```bash
.venv/bin/python scripts/check_hermes.py   # probes /health, /v1/capabilities, auth, one round-trip run
```

## 3. Configure the voice adapter (`.env` in this repo)

**Topology A (co-located):**

```bash
HERMES_BASE_URL=http://localhost:8642/v1
HERMES_API_KEY=<same secret as API_SERVER_KEY>
HERMES_SESSION_KEY=voice-satellite
HERMES_CONTEXT_SOURCE=file:///home/<you>/.hermes
```

**Topology B (remote — reference deployment on jarlaxle):**

```bash
HERMES_BASE_URL=http://jarlaxle:8642/v1
HERMES_API_KEY=<same secret as API_SERVER_KEY>
HERMES_SESSION_KEY=voice-satellite
HERMES_CONTEXT_SOURCE=ssh://aiden@jarlaxle/home/aiden/.hermes
HERMES_CONTEXT_POLL_INTERVAL=30
```

For `ssh://`, the adapter machine needs non-interactive key auth to the Hermes
host (`ssh aiden@jarlaxle true` must succeed — pre-seed `~/.ssh/known_hosts`
on headless boxes).

Optional knobs (defaults shown): `CONTEXT_TOKEN_BUDGET=1200`,
`RESULT_SPEECH_BUDGET=600`, `TASK_JOURNAL_PATH=~/.voice_adapter/tasks.json`,
`HONCHO_API_URL=` (empty = disabled).

## 4. Run the stack

```bash
scripts/run_voice_stack.sh      # Genie TTS + voice_adapter
python satellite.py             # on the edge device (wakeword listener)
```

## 5. Validate (maps to spec user stories)

1. **US1 / MVP** — wakeword → "what's on my schedule today?" → hear a working
   clip + "Let me check…" immediately → keep the session open → result is
   spoken proactively when the run completes.
2. **US2** — while that runs, chat normally and add "could you also summarize
   my emails?" → both tracked; results spoken one at a time, never overlapping.
3. **US3 persistence** — tell it "remember my dog's name is Rex" (this reaches
   Hermes), restart the Hermes process, ask again later → remembered.
4. **US4 context** — once `USER.md` mentions Rex, "what's my dog's name?" is
   answered *locally* (no run dispatched; check adapter logs).
5. **US5** — "is that done yet?" answered from task tags; "cancel that" stops a
   cancellable run.

## Troubleshooting

| Symptom | Check |
|---|---|
| `check_hermes.py` fails on `/health` | gateway running? `API_SERVER_HOST` bound to the interface you're calling? Tailscale up? |
| 401s | `HERMES_API_KEY` ≠ `API_SERVER_KEY` |
| Dispatch works but no proactive result | adapter logs: runs API present in capabilities? if not, you're on the chat fallback — result arrives when the stream closes |
| Context block empty | `HERMES_CONTEXT_SOURCE` URI typo; `ssh` key auth; files exist? (`MEMORY.md`/`USER.md` appear after Hermes has something to remember) |
| Unauthenticated-API warning at startup | set `API_SERVER_KEY` on the Hermes host |
