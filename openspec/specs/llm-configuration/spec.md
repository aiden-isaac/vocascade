# llm-configuration Specification

## Purpose
TBD - created by archiving change de-personalize-byok. Update Purpose after archive.
## Requirements
### Requirement: No personal infrastructure in defaults
The source tree SHALL NOT contain any personal endpoint, hostname, username,
or deployment-specific model name as a runtime default. In particular,
`LLM_BASE_URL`, `LLM_MODEL`, and `HERMES_BASE_URL` SHALL have no baked-in
default value in `config.py`, `setup_server.py`, `gateway/local_llm.py`, or
any other live code path.

#### Scenario: Fresh clone points nowhere
- **WHEN** the repo is cloned and grepped for `frizzt`, `jarlaxle`, or
  `aiden-isaac` outside `docs/legacy-specs/` and git history
- **THEN** no live source, script, test, or example config matches

### Requirement: LLM connection is required and fails fast
`load_config()` SHALL raise a `ValueError` naming the missing `.env` key and
pointing at the setup GUI (`python -m vocascade.setup_server`) when
`LLM_BASE_URL` or `LLM_MODEL` is unset or empty. `LLM_API_KEY` SHALL remain
optional (local endpoints often require none).

#### Scenario: Missing LLM_BASE_URL aborts startup with a located message
- **WHEN** the server starts with no `LLM_BASE_URL` in the environment or `.env`
- **THEN** startup fails before serving, and the error message names
  `LLM_BASE_URL` and mentions the setup GUI

#### Scenario: Key-less local endpoint is accepted
- **WHEN** `LLM_BASE_URL` and `LLM_MODEL` are set and `LLM_API_KEY` is empty
- **THEN** the configuration loads and requests are sent without an
  Authorization header

### Requirement: One code path for cloud and local endpoints
The system SHALL use a single OpenAI-compatible client for the fast brain,
configured only by base URL, optional API key, and model name — with no
provider-specific branches for cloud (OpenRouter, Gemini OpenAI-compat)
versus local (Ollama, llama.cpp-server) endpoints.

#### Scenario: Switching provider is a config-only change
- **WHEN** `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` are changed from a local
  endpoint to a cloud endpoint
- **THEN** no code change is required and the same client performs the calls

### Requirement: Hermes is optional; absence enables local-only mode
When `HERMES_BASE_URL` is unset or empty, the server SHALL start and operate
without building the Hermes run client, task broker, or hermes waterfall
stage. A `hermes` entry in the configured waterfall stages SHALL be dropped
with a startup log line, not an error, so the shipped example configs work
without a Hermes endpoint.

#### Scenario: Server runs without Hermes
- **WHEN** the server starts with `HERMES_BASE_URL` empty and the example
  `config.yaml` listing `hermes` in the waterfall stages
- **THEN** startup succeeds, the health report shows Hermes as not configured,
  and utterances are handled by the remaining stages

#### Scenario: Hermes configured behaves as before
- **WHEN** `HERMES_BASE_URL` is set
- **THEN** the hermes stage, run client, and task broker are built and the
  async-dispatch behavior is unchanged

