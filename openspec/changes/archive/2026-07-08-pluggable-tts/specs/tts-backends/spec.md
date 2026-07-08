# tts-backends

## ADDED Requirements

### Requirement: TTS backend protocol
The system SHALL define a single `TTSBackend` protocol capturing exactly the
surface the pipeline TTS stage consumes: streamed mono s16le PCM chunks from
`synthesize(text)`, a declared native `sample_rate`, a `degraded_mode` flag,
and `start()` / `stop()` / `close()` lifecycle calls. The TTS pipeline stage
SHALL depend only on this protocol, never on a concrete backend class.

#### Scenario: Stage is backend-agnostic
- **WHEN** the TTS stage is constructed with any protocol-conforming backend
  (including a test fake)
- **THEN** text frames are synthesized, effects/volume applied, and audio
  frames emitted with no backend-specific code path in the stage

### Requirement: Backend registry with config selection
The system SHALL keep a plain in-source registry (dict) mapping backend names
to factories, containing `piper` and `genie`. The `TTS_BACKEND` config value
SHALL select the backend by name; an unknown name SHALL fail startup fast with
a located message listing the registered names. Selecting one backend SHALL
NOT import the other backend's third-party dependencies.

#### Scenario: Backend selected by name
- **WHEN** `TTS_BACKEND=genie` is set with valid Genie configuration
- **THEN** the existing Genie client is constructed and behaves exactly as
  before this change

#### Scenario: Unknown backend fails fast
- **WHEN** `TTS_BACKEND=espeak` (unregistered) is set
- **THEN** the server refuses to start with a message naming the bad value,
  the config location, and the registered backend names

#### Scenario: Genie-only install does not need piper installed
- **WHEN** `TTS_BACKEND=genie` is selected and the `piper` package is not
  installed
- **THEN** the server starts and speaks normally

### Requirement: Piper is the zero-setup default voice
With no TTS configuration at all, the system SHALL default to the Piper
backend with a stock default voice and produce spoken audio. Stock male and
female voices SHALL be selectable by friendly name via `TTS_VOICE`. Piper
synthesis SHALL run on CPU and SHALL NOT block the event loop.

#### Scenario: Fresh install speaks
- **WHEN** the server starts with a `.env` containing no `TTS_*` or `GENIE_*`
  keys and the default voice model is available
- **THEN** replies are spoken using the Piper default voice

#### Scenario: Voice selected by friendly name
- **WHEN** `TTS_VOICE=male` is set with the Piper backend
- **THEN** synthesis uses the stock male voice

### Requirement: Output sample rate is normalized to the wire rate
Each backend SHALL declare the native sample rate of its PCM output. When the
native rate differs from the configured `audio_out_sample_rate`, the TTS stage
SHALL resample so every emitted audio frame carries the configured wire rate.

#### Scenario: Piper native rate resampled
- **WHEN** the selected backend declares 22050 Hz and
  `audio_out_sample_rate` is 32000
- **THEN** emitted audio frames contain 32000 Hz PCM stamped with 32000

### Requirement: Backend failure degrades to text-only with a located message
The system SHALL enter the existing text-only degraded mode when the selected
backend cannot initialize (missing Piper voice files that cannot be fetched,
unreachable Genie server), logging a message that names the backend, the
failing resource, and the fix. The startup health report SHALL probe the
selected backend without blocking startup. If Piper voice files are absent at
start, the client SHALL make one bounded best-effort attempt to download the
stock voice before degrading.

#### Scenario: Missing Piper voice degrades with guidance
- **WHEN** the Piper voice files are absent and the download attempt fails
- **THEN** the server runs text-only and the log names the voice, the models
  directory, and the command to install the voice

#### Scenario: Genie degradation unchanged
- **WHEN** `TTS_BACKEND=genie` and the Genie server is unreachable
- **THEN** existing degraded-mode behavior applies unchanged

### Requirement: Setup GUI exposes backend and voice selection
The setup GUI voice tab SHALL offer backend selection (from the registry) and
voice name, persisting `TTS_BACKEND` and `TTS_VOICE` to `.env` alongside the
existing Genie keys. Genie-specific fields SHALL be presented as applying only
to the genie backend.

#### Scenario: GUI writes backend selection
- **WHEN** the user selects the piper backend and a voice in the setup GUI and
  saves
- **THEN** `.env` contains `TTS_BACKEND=piper` and the chosen `TTS_VOICE`

### Requirement: Filler generation uses the configured backend
`scripts/generate_fillers.py` SHALL render filler audio through the configured
backend via the registry, so pre-rendered fillers match the active voice.

#### Scenario: Fillers rendered with Piper
- **WHEN** filler generation runs with `TTS_BACKEND=piper`
- **THEN** the produced filler audio is synthesized by the Piper voice, not
  Genie
