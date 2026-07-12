# packaging Specification

## Purpose
TBD - created by archiving change packaging-deploy. Update Purpose after archive.
## Requirements
### Requirement: Installable package with console entry points

The project SHALL provide a `pyproject.toml` such that `pip install .` in a
clone installs the `vocascade` package (Python >= 3.11) and creates two console
scripts: `vocascade` launching the host server (equivalent to
`python -m vocascade`) and `vocascade-edge` launching the edge client
(equivalent to `python -m vocascade.edge`). The `python -m` entry points SHALL
remain functional.

#### Scenario: pip install from a fresh clone

- **WHEN** a user runs `pip install .` in a fresh clone with Python >= 3.11
- **THEN** the install succeeds without `PYTHONPATH` tricks, and `vocascade`
  and `vocascade-edge` are on `PATH` and launch the server and edge client

#### Scenario: legacy module invocation still works

- **WHEN** a user runs `python -m vocascade` or `python -m vocascade.edge` in
  an environment where the package is installed
- **THEN** they behave identically to the console scripts

### Requirement: Complete dependency declaration with edge extra

`pyproject.toml` SHALL declare every runtime dependency of the host under base
dependencies (including `piper-tts` pinned to an exact version), and the edge
client's capture dependencies (pyaudio, openwakeword, onnxruntime) under an
optional extra named `edge`. No runtime import in the codebase SHALL lack a
corresponding declared dependency. The stopgap `piper-tts` line in
`requirements.txt` SHALL be removed, and `requirements.txt` retired in favor
of `pip install -e .` (CI and README updated in the same change).

#### Scenario: host install runs without edge packages

- **WHEN** a user installs with `pip install .` (no extras) and starts the host
- **THEN** the server starts and speaks via the default piper backend without
  pyaudio, openwakeword, or onnxruntime installed

#### Scenario: edge extra pulls capture dependencies

- **WHEN** a user runs `pip install .[edge]`
- **THEN** pyaudio, openwakeword, and onnxruntime are installed and
  `vocascade-edge` runs without ModuleNotFoundError

#### Scenario: no undeclared runtime imports

- **WHEN** the test suite and both entry points run in a clean environment
  containing only the declared base (plus `edge` for the edge client) deps
- **THEN** no third-party import fails

### Requirement: License file

The repository SHALL contain a `LICENSE` file at the root, referenced from
`pyproject.toml` metadata and the README. The working choice is AGPL-3.0 and
MUST be explicitly confirmed by the project owner before the change merges.

#### Scenario: license present and consistent

- **WHEN** a user inspects the repo root or the installed package metadata
- **THEN** the same license is stated in `LICENSE`, `pyproject.toml`, and README

