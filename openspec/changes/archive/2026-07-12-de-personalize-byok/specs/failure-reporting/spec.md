# failure-reporting

Spoken and printed failure surfaces for a misconfigured or unreachable LLM:
startup endpoint probes in the health report, spoken LLM-failure notices,
spoken waterfall-exhaustion notice, and once-per-session throttling. Extends
the existing per-stage graceful degradation (US7) with diagnosis.

## ADDED Requirements

### Requirement: LLM failures are classified
The fast-brain LLM client SHALL distinguish authentication failures
(HTTP 401/403) from unreachability (connection error, timeout, 5xx) using
distinct exception types sharing a common base, so all callers and reporting
surfaces consume one classification.

#### Scenario: 401 classifies as auth failure
- **WHEN** a chat call receives HTTP 401
- **THEN** the client raises the authentication-failure type

#### Scenario: Connection refused classifies as unreachable
- **WHEN** a chat call cannot connect to the endpoint
- **THEN** the client raises the unreachable type

### Requirement: Startup health report probes endpoints
On server startup, the health report SHALL probe the configured LLM endpoint
with a single short-timeout request and print one of: reachable,
authentication rejected, or unreachable. When Hermes is configured, its
capabilities probe verdict SHALL be printed likewise. A failed probe SHALL
warn but SHALL NOT abort startup.

#### Scenario: Wrong key visible at startup
- **WHEN** the server starts with an LLM key its endpoint rejects
- **THEN** the health report prints an authentication-rejected verdict for
  the LLM endpoint and the server still starts

#### Scenario: Endpoint down at startup does not block
- **WHEN** the server starts while the LLM endpoint is down
- **THEN** the health report prints unreachable within the probe timeout and
  the server still starts

### Requirement: First LLM failure in a session is spoken specifically
The assistant SHALL speak a notice naming the problem class (unreachable vs.
key rejected) the first time in a voice session that a fast-brain call fails
with a classified error, instead of the generic fallback. Subsequent
classified failures in the same session SHALL NOT repeat the specific notice.

#### Scenario: Unreachable LLM is announced once
- **WHEN** the fast brain is unreachable and the user speaks two consecutive
  smalltalk utterances
- **THEN** the first reply says the language model cannot be reached, and the
  second uses the existing generic fallback

#### Scenario: New session resets the notice
- **WHEN** a new voice session starts after a session in which the notice was
  spoken
- **THEN** the next classified failure is announced specifically again

### Requirement: Waterfall exhaustion is never silent
The assistant SHALL speak a short notice that it cannot help with a request
when every waterfall stage abstains and no hermes stage is configured
(local-only mode), rather than ending the turn without output.

#### Scenario: Data question in local-only mode
- **WHEN** Hermes is not configured and the user asks a question every local
  stage abstains from
- **THEN** the assistant speaks a can't-help notice instead of staying silent
