# wake-word-default Specification

## Purpose
TBD - created by archiving change packaging-deploy. Update Purpose after archive.
## Requirements
### Requirement: Permissive default wake word works on a fresh clone

The edge client's default `WAKE_WORD_MODEL` SHALL be a permissively licensed
openwakeword pre-trained model referenced by bare name (default: `hey_jarvis`),
not a file path. On first run the edge client SHALL provision the model
automatically (from the installed openwakeword package or by download to
`~/.vocascade/wakeword/`) before the capture loop starts, logging what it did.
The personal `static/wakeword/eden_wakeword.onnx` default SHALL be removed in
the same change — at no commit does a fresh clone lack a working wake word.

#### Scenario: fresh install wakes with no manual model step

- **WHEN** a user installs the edge client on a fresh machine with network
  access and runs it without setting `WAKE_WORD_MODEL`
- **THEN** the default model is provisioned automatically and the wake word
  activates the assistant

#### Scenario: provisioning failure is actionable

- **WHEN** first-run provisioning fails (e.g. no network and model not cached)
- **THEN** the edge client exits with a clear error naming the model, the cache
  path, and the manual step to fix it — not a stack trace from the capture loop

### Requirement: Path override preserved for custom models

A `WAKE_WORD_MODEL` value that is an existing file path SHALL be used as-is
with no provisioning attempted, so existing installs and custom-trained models
(including the author's personal model) keep working unchanged and fully
offline.

#### Scenario: existing .env with a model path is unaffected

- **WHEN** `WAKE_WORD_MODEL` points at an existing `.onnx` file
- **THEN** the edge client loads exactly that file and performs no download

