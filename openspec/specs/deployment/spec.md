# deployment Specification

## Purpose
TBD - created by archiving change packaging-deploy. Update Purpose after archive.
## Requirements
### Requirement: Docker host image is the flagship install

The repository SHALL provide a `Dockerfile` and `docker-compose.yaml` that run
the host server CPU-only on x86_64 and aarch64 Linux. The container SHALL read
secrets/endpoints from an env file and `config.yaml` from a mount, and SHALL
persist downloaded models (TTS voices, STT models) in a volume so restarts do
not re-download. The edge client SHALL NOT be containerized or documented as
containerizable.

#### Scenario: compose up serves the host

- **WHEN** a user with a configured `.env` and `config.yaml` runs
  `docker compose up`
- **THEN** the host server starts, exposes the WebSocket endpoint on the
  configured port, and an edge client on the LAN can connect and converse

#### Scenario: models survive container recreation

- **WHEN** the container is removed and recreated with the same volume
- **THEN** previously downloaded TTS/STT models are reused without re-download

### Requirement: Native edge install with systemd unit

The edge client SHALL install natively via `pip install vocascade[edge]`
without needing a repo checkout at runtime, and the repository SHALL ship a
systemd user unit (`deploy/vocascade-edge.service`) plus README instructions
that start the edge client at login with access to the user's audio session.

#### Scenario: edge runs from a bare pip install

- **WHEN** a user installs the `[edge]` extra in a clean venv and runs
  `vocascade-edge` with a configured `.env`
- **THEN** the client provisions its wake word, connects to the host, and
  streams audio — with no files needed from a repo checkout

#### Scenario: systemd user unit keeps edge running

- **WHEN** a user installs and enables the shipped unit per the README
- **THEN** the edge client starts at login, restarts on failure, and can play
  and capture audio via the user session (PipeWire/PulseAudio)

### Requirement: Single-box quickstart is the documented default

The README SHALL lead with a quickstart that runs host and edge on one Linux
machine over localhost, from fresh clone to a spoken answer, using only
documented commands (including any OS-level prerequisites such as portaudio).

#### Scenario: stranger completes the quickstart

- **WHEN** a user on a supported Linux box follows only the quickstart section
- **THEN** they reach a working wake-word → question → spoken-answer loop
  without undocumented steps or author assistance

### Requirement: Honest beta limitations documentation

The README SHALL contain a beta-limitations section stating plainly: exactly
one satellite/session at a time (concurrent `/ws` connects are rejected with
close code 1008; multi-session is planned for v1.1), the edge client is tested
on Linux only, and the assistant is English-only.

#### Scenario: limitations are discoverable before install

- **WHEN** a prospective user reads the README top matter
- **THEN** the single-session, Linux-edge, and English-only limits are stated
  before the install instructions, not buried in a footnote

