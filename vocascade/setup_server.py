"""
vocascade/setup_server.py — a small localhost setup GUI for the voice stack.

Standalone FastAPI app (does NOT boot the voice pipeline) that reads/writes the
config files a human sets up by hand: `.env`, `config.yaml`, `static/fillers.json`,
and the `genie_profiles/<name>/` voice directories. Run it with:

    python -m vocascade.setup_server      # -> http://127.0.0.1:8099

# ponytail: 127.0.0.1 bind only — it writes files and execs generate_fillers.py.
# It is a single-user dev tool; add auth only if it is ever exposed off-localhost.
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path

import yaml
from dotenv import dotenv_values, set_key
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
CONFIG_PATH = ROOT / os.getenv("VOCASCADE_CONFIG_PATH", "config.yaml")
FILLERS_PATH = ROOT / "static" / "fillers.json"
SETUP_HTML = ROOT / "static" / "setup.html"
PROFILES_DIR = ROOT / "genie_profiles"

# Env vars a human actually sets, grouped for the UI: (key, default, blurb).
# Names/defaults mirror the os.getenv(...) calls in vocascade/config.py — that
# file is the source of truth. BYOK (D1/D7): the LLM group is the first-run
# essential and ships with NO defaults; Hermes is optional (empty = local-only).
ENV_GROUPS: dict[str, list[tuple[str, str, str]]] = {
    "LLM (required)": [
        ("LLM_BASE_URL", "",
         "Any OpenAI-compatible endpoint, including /v1. Local: http://localhost:11434/v1 "
         "(Ollama), http://localhost:8080/v1 (llama.cpp-server). Cloud: "
         "https://openrouter.ai/api/v1, https://generativelanguage.googleapis.com/v1beta/openai."),
        ("LLM_API_KEY", "",
         "API key for the endpoint above. Leave empty for local endpoints that need none."),
        ("LLM_MODEL", "",
         "Model name to request, e.g. llama3.2 (Ollama) or anthropic/claude-haiku-4-5 (OpenRouter)."),
    ],
    "Hermes agent (optional)": [
        ("HERMES_BASE_URL", "",
         "Hermes agent endpoint including /v1. Leave empty to run local-only "
         "(skills + smalltalk, no agent fallback)."),
        ("HERMES_API_KEY", "", ""),
        ("HERMES_MODEL", "hermes-agent", ""),
        ("HERMES_SESSION_KEY", "voice-satellite", ""),
    ],
    "Service": [
        ("HOST", "0.0.0.0", ""),
        ("PORT", "8000", ""),
        ("AUDIO_IN_SAMPLE_RATE", "16000", ""),
        ("AUDIO_OUT_SAMPLE_RATE", "32000", ""),
    ],
    "Speech-to-text": [
        ("WHISPER_MODEL", "tiny.en", ""),
        ("WHISPER_LANGUAGE", "en", ""),
    ],
}
# Voice/TTS keys live on their own tab.
VOICE_KEYS: list[tuple[str, str]] = [
    ("GENIE_TTS_URL", "http://127.0.0.1:8000"),
    ("GENIE_CHARACTER_NAME", "default"),
    ("GENIE_ONNX_MODEL_DIR", ""),
    ("GENIE_REFERENCE_AUDIO", ""),
    ("GENIE_REFERENCE_TEXT", ""),
    ("GENIE_LANGUAGE", "en"),
]
# Sensitivity/tuning knobs (issue #171) — env-driven, each with a plain-english
# blurb shown on its own tab. Defaults mirror config.py / edge/__main__.py.
TUNING_KEYS: list[tuple[str, str, str]] = [
    ("WAKE_WORD_THRESHOLD", "0.5",
     "Wake-word sensitivity (0–1) — lower triggers more easily but with more false alarms."),
    ("VAD_THRESHOLD", "0.5",
     "Speech-detection sensitivity (0–1) when server VAD is on — lower hears quieter speech but also more noise."),
    ("VAD_MIN_SILENCE_MS", "250",
     "How long you pause (milliseconds) before it decides you've stopped talking — higher is more patient."),
    ("VAD_SPEECH_PAD_MS", "50",
     "Extra audio (milliseconds) kept around each utterance so the first/last words aren't clipped."),
    ("WHISPER_BEAM_SIZE", "1",
     "Transcription search width — higher is a little more accurate but slower; 1 is fastest."),
    ("WHISPER_VAD_FILTER", "false",
     "Let the transcriber skip silent/non-speech audio (true/false)."),
    ("TTS_VOLUME", "1.0",
     "Output loudness multiplier — 1.0 is normal, 2.0 is twice as loud, 0.5 is half."),
]
KNOWN_KEYS = ({k for grp in ENV_GROUPS.values() for k, _, _ in grp}
              | {k for k, _ in VOICE_KEYS} | {k for k, _, _ in TUNING_KEYS})


def _is_secret(key: str) -> bool:
    return key.endswith("_KEY") and "API" in key


# --- config.yaml surgical editors (preserve comments; no ruamel dependency) ---

def require_sections(parsed) -> None:
    """Mirror the validation in vocascade/config.py:load_config."""
    if not parsed:
        raise ValueError("config is empty")
    for section in ("system", "waterfall", "skills"):
        if section not in parsed:
            raise ValueError(f"missing required section: '{section}'")


def replace_block_list(text: str, key: str, new_items: list[str]) -> str:
    """Reorder a contiguous block-style YAML list (`key:` then `  - item` lines),
    leaving every other line (comments included) untouched."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.strip() == f"{key}:":
            break
    else:
        raise ValueError(f"'{key}:' not found in config")

    item_re = re.compile(r"^(\s+)-\s+(.*\S)\s*$")
    indent, j = None, i + 1
    while j < len(lines) and (m := item_re.match(lines[j])):
        if indent is None:
            indent = m.group(1)
        j += 1
    if indent is None:
        raise ValueError(f"no list items under '{key}:'")

    lines[i + 1:j] = [f"{indent}- {it}" for it in new_items]
    return "\n".join(lines)


def set_scalar(text: str, key: str, value) -> str:
    """Replace the value of the first `  key: ...` scalar line, keeping indent
    and any trailing comment."""
    pat = re.compile(rf"^(\s*){re.escape(key)}:[ \t]*[^#\n]*?(\s*#.*)?$", re.M)
    new_text, n = pat.subn(lambda m: f"{m.group(1)}{key}: {value}{m.group(2) or ''}", text, count=1)
    if n == 0:
        raise ValueError(f"scalar '{key}:' not found")
    return new_text


def valid_fillers(obj) -> bool:
    return isinstance(obj, dict) and all(
        isinstance(v, list) and all(isinstance(s, str) for s in v) for v in obj.values()
    )


app = FastAPI(title="Vocascade Setup")


@app.get("/")
async def index() -> HTMLResponse:
    return HTMLResponse(SETUP_HTML.read_text(encoding="utf-8"))


# --- .env ---

@app.get("/api/env")
async def get_env() -> dict:
    current = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}

    def field(key: str, default: str, blurb: str = "") -> dict:
        return {
            "key": key,
            "value": current.get(key) if current.get(key) is not None else default,
            "default": default,
            "secret": _is_secret(key),
            "blurb": blurb,
        }

    return {
        "groups": {g: [field(k, d, b) for k, d, b in fields] for g, fields in ENV_GROUPS.items()},
        "voice": [field(k, d) for k, d in VOICE_KEYS],
        "tuning": [field(k, d, b) for k, d, b in TUNING_KEYS],
    }


@app.post("/api/env")
async def set_env(request: Request) -> dict:
    values = (await request.json()).get("values", {})
    if not ENV_PATH.exists():
        ENV_PATH.touch()
    written = []
    for key, val in values.items():
        if key not in KNOWN_KEYS:
            continue
        set_key(str(ENV_PATH), key, str(val), quote_mode="auto")
        written.append(key)
    return {"ok": True, "written": written}


# --- connection tests (D7): probe a candidate config WITHOUT writing .env ---

@app.post("/api/test-llm")
async def test_llm(request: Request) -> dict:
    """Verdicts: ok | auth | unreachable | error (+detail). Bounded timeout."""
    from vocascade.gateway.local_llm import LocalLLM, LLMAuthError, LLMUnreachableError

    body = await request.json()
    base_url = (body.get("base_url") or "").strip()
    model = (body.get("model") or "").strip()
    if not base_url or not model:
        raise HTTPException(400, "base_url and model are required")
    llm = LocalLLM(base_url=base_url, model=model,
                   api_key=(body.get("api_key") or "").strip() or None, timeout=5.0)
    try:
        await llm.chat([{"role": "user", "content": "ping"}], max_tokens=1)
        return {"verdict": "ok"}
    except LLMAuthError as e:
        return {"verdict": "auth", "detail": str(e)}
    except LLMUnreachableError as e:
        return {"verdict": "unreachable", "detail": str(e)}
    except Exception as e:
        return {"verdict": "error", "detail": str(e)}


@app.post("/api/test-hermes")
async def test_hermes(request: Request) -> dict:
    """Probe a Hermes /v1/capabilities directly so auth is distinguishable."""
    import httpx

    body = await request.json()
    base_url = (body.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise HTTPException(400, "base_url is required")
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    headers = {}
    api_key = (body.get("api_key") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url}/capabilities", headers=headers)
    except httpx.TransportError as e:
        return {"verdict": "unreachable", "detail": str(e)}
    if resp.status_code in (401, 403):
        return {"verdict": "auth", "detail": f"HTTP {resp.status_code}"}
    if resp.status_code != 200:
        return {"verdict": "error", "detail": f"HTTP {resp.status_code}"}
    try:
        features = resp.json().get("features", {})
    except ValueError:
        return {"verdict": "error", "detail": "endpoint answered but not with Hermes capabilities"}
    return {"verdict": "ok", "runs_api": bool(features.get("run_submission"))}


# --- voices ---

@app.get("/api/voices")
async def get_voices() -> dict:
    voices = []
    if PROFILES_DIR.is_dir():
        for d in sorted(p for p in PROFILES_DIR.iterdir() if p.is_dir()):
            voices.append({
                "name": d.name,
                "export_dir": str(d / "export") if (d / "export").is_dir() else None,
                "wavs": sorted(str(w) for w in d.glob("*.wav")),
            })
    return {"voices": voices}


@app.post("/api/voice/upload")
async def upload_voice(request: Request, name: str, filename: str) -> dict:
    name, filename = Path(name).name, Path(filename).name
    if name in ("", ".", "..") or not filename.lower().endswith(".wav"):
        raise HTTPException(400, "name required and filename must be a .wav")
    dest_dir = PROFILES_DIR / name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    dest.write_bytes(await request.body())
    return {"path": str(dest)}


# --- fillers ---

@app.get("/api/fillers")
async def get_fillers() -> dict:
    if not FILLERS_PATH.exists():
        return {}
    return json.loads(FILLERS_PATH.read_text(encoding="utf-8"))


@app.post("/api/fillers")
async def set_fillers(request: Request) -> dict:
    obj = await request.json()
    if not valid_fillers(obj):
        raise HTTPException(400, "fillers must be an object of category -> list of strings")
    FILLERS_PATH.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    return {"ok": True}


@app.post("/api/fillers/regenerate")
async def regenerate_fillers() -> PlainTextResponse:
    venv_py = ROOT / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.exists() else sys.executable
    proc = await asyncio.create_subprocess_exec(
        py, "scripts/generate_fillers.py", cwd=str(ROOT),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    out, _ = await proc.communicate()
    return PlainTextResponse(out.decode(errors="replace"), status_code=200 if proc.returncode == 0 else 500)


# --- config.yaml (advanced raw editor) ---

@app.get("/api/config-yaml")
async def get_config_yaml() -> PlainTextResponse:
    return PlainTextResponse(CONFIG_PATH.read_text(encoding="utf-8") if CONFIG_PATH.exists() else "")


@app.post("/api/config-yaml")
async def set_config_yaml(request: Request) -> dict:
    text = (await request.json()).get("text", "")
    try:
        parsed = yaml.safe_load(text)
        require_sections(parsed)
    except yaml.YAMLError as e:
        raise HTTPException(400, f"YAML parse error: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    CONFIG_PATH.write_text(text, encoding="utf-8")
    return {"ok": True}


# --- waterfall (drag-reorder stages + edit thresholds, comments preserved) ---

@app.get("/api/waterfall")
async def get_waterfall() -> dict:
    wf = (yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}).get("waterfall", {})
    return {"stages": wf.get("stages", []), "thresholds": wf.get("thresholds", {})}


@app.post("/api/waterfall")
async def set_waterfall(request: Request) -> dict:
    data = await request.json()
    text = CONFIG_PATH.read_text(encoding="utf-8")
    cur = (yaml.safe_load(text) or {}).get("waterfall", {})
    cur_stages = cur.get("stages", [])

    new_stages = data.get("stages", cur_stages)
    if sorted(new_stages) != sorted(cur_stages):
        raise HTTPException(400, "stages must be a reordering of the existing stages")
    text = replace_block_list(text, "stages", new_stages)

    for key, val in (data.get("thresholds") or {}).items():
        try:
            text = set_scalar(text, key, float(val))
        except ValueError as e:
            raise HTTPException(400, str(e))

    try:
        require_sections(yaml.safe_load(text))
    except (yaml.YAMLError, ValueError) as e:
        raise HTTPException(400, f"resulting config invalid: {e}")
    CONFIG_PATH.write_text(text, encoding="utf-8")
    return {"ok": True}


# --- skills (read-only view; discovers bundled + user_skills live on disk) ---

_skills_ready = False


def _ensure_skills() -> None:
    """Discover skills once into the process-wide registry. Skill modules are
    light imports; discovery is isolated (a broken file is logged, not raised)."""
    global _skills_ready
    if _skills_ready:
        return
    from vocascade.skills.registry import registry

    registry.discover_bundled_skills()
    registry.discover_user_skills(str(ROOT / "user_skills"))
    _skills_ready = True


@app.get("/api/skills")
async def get_skills() -> dict:
    _ensure_skills()
    from vocascade.skills.registry import registry

    cfg = {}
    if CONFIG_PATH.exists():
        cfg = (yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}).get("skills", {}) or {}
    out = []
    for s in registry.get_all_skills():
        sc = cfg.get(s.name, {}) or {}
        out.append({
            "name": s.name,
            "source": s.source,
            "examples": s.examples or [],
            "keywords": s.keywords or [],
            "enabled": sc.get("enabled", True),
            "filler": sc.get("filler"),
        })
    out.sort(key=lambda x: (x["source"] != "user", x["name"]))  # user skills first
    return {"skills": out}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("SETUP_PORT", "8099"))
    print(f"Vocascade setup UI → http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port)
