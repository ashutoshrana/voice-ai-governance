# Changelog

All notable changes to this project are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

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

## [Unreleased]

### Planned
- LiveKit Agents adapter
- Amazon Connect adapter
- CCPA opt-out enforcement (California Consumer Privacy Act)
- Twilio Programmable Messaging bulk compliance
