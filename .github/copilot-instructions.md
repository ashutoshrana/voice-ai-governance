# Copilot Instructions — voice-ai-governance

## Project Purpose
`voice-ai-governance` is a Python library implementing compliance controls for voice AI pipelines. It enforces TCPA consent, scrubs HIPAA PHI from transcripts, provides EU AI Act Article 13 AI disclosure, and preserves call state for warm-transfer handoffs.

## Core Concepts
- **TCPA Consent Lifecycle State Machine** — tracks opt-in/opt-out; blocks AI-initiated calls without documented consent (47 U.S.C. § 227)
- **HIPAA PHI Scrubber** — removes all 18 Safe Harbor identifiers from transcripts (45 CFR § 164.514(b))
- **EU AI Act Voice Policy (EUAIActVoicePolicy)** — Art. 13 disclosure at session start + Art. 14 warm-transfer-to-human override
- **Warm Transfer State Preservation (WTSP)** — serializes session state so human agent continues without repeat questions

## Package Structure
```
src/voice_ai_governance/
  consent.py         — ConsentRecord, TCPAConsentManager
  phi_scrubber.py    — PHIScrubber (18 Safe Harbor identifiers)
  eu_policy.py       — EUAIActVoicePolicy
  warm_transfer.py   — WarmTransferStatePreserver
  session.py         — VoiceSession, SessionAuditLog
examples/
  tcpa_hipaa_voice_session.py   — working full example
tests/
  test_consent.py, test_phi_scrubber.py, test_eu_policy.py, test_warm_transfer.py
```

## Code Conventions
- All policy objects are `@dataclass(frozen=True)` — immutable by design
- Every voice session produces an audit log: session_id, caller_id (hashed), consent_status, phi_detected, escalation_reason, transfer_state
- PHI scrubber must handle all 18 Safe Harbor identifiers — no partial implementation
- Warm transfer state is JSON-serializable (no live objects)
- Tests use `pytest`; PHI scrubber tests use synthetic data only (no real health data)

## Regulatory Citations
- TCPA 47 U.S.C. § 227 — consent required before AI-initiated voice contact
- HIPAA 45 CFR § 164.514(b) — 18 Safe Harbor de-identification identifiers
- EU AI Act Art. 13 — transparency (AI must disclose it is AI)
- EU AI Act Art. 14 — human oversight; warm transfer = override mechanism

## What NOT to Include
- No real patient data, call recordings, or PII in tests or examples
- No customer/client names (SEI, Capella, Strayer) or product names (ELLA, Falcon, Polaris)
- No production telephony credentials (Twilio, Genesys account IDs)
- Patterns must work with any telephony provider — adapters live in `adapters/`

## PR Standards
- PR title: conventional commits — `feat: add Twilio adapter` / `fix: HIPAA scrubber edge case for DOB`
- Every new telephony adapter needs: implementation + tests + README entry
- PHI scrubber changes must include tests covering all 18 identifier types
