# setup-gui

The first-run setup flow: BYOK collection (base URL, key, model), connection
testing, and what the GUI writes to `.env`.

## ADDED Requirements

### Requirement: LLM connection is the first-run essential
The setup GUI SHALL present the Local LLM group (base URL, API key, model)
first among the environment groups, with empty defaults and per-field blurbs
that include example values for common endpoints (Ollama, llama.cpp-server,
OpenRouter, Gemini OpenAI-compat). The Hermes group SHALL be presented as
optional.

#### Scenario: Fresh setup shows no personal values
- **WHEN** the setup GUI is opened with no `.env` present
- **THEN** the LLM fields are empty (no pre-filled endpoint or model name)
  and their blurbs show example values for cloud and local providers

### Requirement: Connection test before saving
The setup GUI SHALL provide a test-connection action that probes a candidate
LLM configuration (base URL, key, model) without writing `.env`, and reports
one of three verdicts: reachable, authentication rejected, or unreachable.
An equivalent test SHALL be available for a candidate Hermes endpoint using
its capabilities probe.

#### Scenario: Bad key is distinguished from bad URL
- **WHEN** the user tests a configuration whose endpoint answers HTTP 401
- **THEN** the GUI reports an authentication problem, not a generic failure

#### Scenario: Unreachable endpoint reported without hanging
- **WHEN** the user tests a configuration whose endpoint does not respond
- **THEN** the GUI reports the endpoint as unreachable within a bounded
  timeout

### Requirement: GUI-written config is sufficient to run
Values saved through the setup GUI SHALL be sufficient for the server to pass
`load_config()` validation for the LLM connection — a user who completes the
LLM group in the GUI and saves SHALL NOT need to hand-edit `.env` to start
the server.

#### Scenario: Save then start
- **WHEN** the user fills base URL and model in the GUI, saves, and starts
  the server
- **THEN** startup passes configuration validation for the LLM connection
