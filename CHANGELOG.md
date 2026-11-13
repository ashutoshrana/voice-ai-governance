# Changelog

All notable changes to this project are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

- LiveKit publication now rejects missing/failing HIPAA policy decisions with `RuntimeError`, without publishing or disconnecting. The confidence hook prechecks before escalation state updates. Migration: supply trusted BAA evidence to the default policy; see `docs/POLICY_SEMANTICS.md`.

- Fix the previously unused BAA requirement: enabled policies now require literal `baa_verified=True`; missing or non-boolean evidence produces a critical failure. Applications must supply a verified attestation or explicitly disable the requirement after assessing applicability.
- Fail closed on missing/non-boolean human-override evidence in configured high-risk EU policies. Honor the PHI-scrubbing option. Add keyword-only `block_on_violation` to HIPAA/EU policies; default advisory behavior for noncritical violations remains compatible.
- Preserve legacy rule IDs while correcting recording-consent and ADMT references. Prefer specific ADMT phrases over generic opt-out matching. Document source dates, policy limits and adapter migration in `docs/POLICY_SEMANTICS.md` and `docs/REGULATORY_SOURCES.md`.
- Correct vacated FCC one-to-one attribution and absolute SMS privacy claims. Synchronize citation metadata with the published 0.5.1 release; this work remains unreleased.

- Correct AI-interaction disclosure references to EU AI Act Article 50(1) in documentation and violation text. Preserve the historical `EUAIA-13.1-01` rule identifier for compatibility; policy decisions are unchanged. Historical release descriptions below are retained.
- Distinguish download activity from independently verified adoption and clarify the scope of configurable compliance checks.

## [0.5.1] — 2026-11-02

- Raise Redis, LiveKit/OpenTelemetry and boto3/urllib3 installer floors after minimum/current graph qualification. Remove the unqualified legacy Pipecat installer from metadata and `all`; retain its caller-wired helper API. No telephony behavior changes.
- Prepare the supported-runtime/release-verification hardening patch; no voice, redaction, consent or telephony behavior changes.
- Recommend a maintained Python 3.11+ production runtime; retain legacy Python 3.9 core compatibility and Python 3.10+ development tooling.

## [0.5.0] — Unreleased

- Development/test tooling now requires Python 3.10+ and patched `pytest>=9.0.3`; the dependency-free base runtime still supports Python 3.9. CI checks Python 3.9 through an isolated installed-wheel smoke and compile check without development dependencies.

- Fail closed when handoff payload redaction is requested without a configured scrubber in both sync and async managers.
- Migration: configure `PIIScrubber` or explicitly pass `scrub_pii=False` when raw payloads are intended. Missing sessions continue to return `None`.

## [0.4.0] — Unreleased

- Require an explicit present recipient for LiveKit handoff-context publication.
- Propagate publication errors and cancellation; publication does not disconnect the caller or claim a connected transfer.
- Keep native call orchestration and recipient authorization with the application; see the README migration note.
- Prepare OIDC publishing and artifact attestations; account configuration and release verification remain required.

## [0.3.1] — 2026-09-19

- Preserve sensitive-field classification when custom patterns redact field names.
- Return isolated in-memory conversation snapshots and redact nested dictionary keys; reject redaction collisions instead of silently losing entries.

## [0.3.0] — 2026-09-11

- Complete handoff-field redaction, including nested sequences and name entities; WATCH/MULTI updates with bounded retries and completed-session protection. Released as 0.3.0.

## [0.2.0] — 2026-04-27

### Added — EU AI Act and A2P 10DLC enforcement

**EU AI Act compliance**
- `EUAIActVoicePolicy` — Article 13 AI disclosure enforcement at session open;
  Article 14 warm-transfer-to-human override capability
- Automatic AI system identification message at start of every voice session

**A2P 10DLC SMS compliance**
- `A2PDLCValidator` — validates campaign registration before sending
  AI-generated content via SMS
- AI-generated content disclosure injection per FCC requirements
- Omnichannel consent bridging: voice consent valid for SMS follow-ups
  within 72-hour window

**Post-call summaries**
- Post-call SMS summary with PHI automatically stripped (HIPAA Minimum
  Necessary Rule 45 CFR § 164.502)

**Tests**
- 79 tests total (up from 40 in v0.1.0)

---

## [0.1.0] — 2026-04-25

### Added — Initial release

**Core compliance engine**
- `VoiceCompliancePolicy` — composable policy stack for voice sessions
- TCPA consent lifecycle: opt-in capture, opt-out interception, quiet hours
  enforcement (47 U.S.C. § 227; 47 CFR § 64.1200)
- HIPAA PHI scrubber — 18 Safe Harbor identifiers + regex patterns
  (45 CFR § 164.514(b))
- FERPA PII scrubber — education record identifiers
  (34 CFR § 99.3)
- Confidence-gated escalation — ASR confidence threshold triggers
  warm transfer to human agent

**Warm transfer state**
- `WarmTransferStateManager` — Redis-backed session state preservation
  across agent-to-human handoff
- Progressive profiling payload: structured context packet delivered
  to receiving human agent
- Race-condition prevention via Redis atomic operations

**Platform adapters**
- Pipecat pipeline integration
- Twilio ConversationRelay integration

**Tests**
- 40 tests across core compliance, TCPA, HIPAA/FERPA scrubbing,
  warm transfer, and platform adapters

---

## Remaining roadmap

### Planned
- CCPA opt-out enforcement (California Consumer Privacy Act)
- Twilio Programmable Messaging bulk compliance
