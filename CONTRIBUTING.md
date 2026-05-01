# Contributing to voice-ai-governance

Thank you for your interest in contributing. This library provides
compliance enforcement middleware for voice AI pipelines, covering TCPA,
HIPAA, FERPA, and EU AI Act requirements for Pipecat, Twilio, and LiveKit.

---

## Table of contents

1. [Development setup](#1-development-setup)
2. [Repository structure](#2-repository-structure)
3. [How to add a new platform adapter](#3-how-to-add-a-new-platform-adapter)
4. [How to add a new compliance policy](#4-how-to-add-a-new-compliance-policy)
5. [PR checklist](#5-pr-checklist)

---

## 1. Development setup

```bash
git clone https://github.com/ashutoshrana/voice-ai-governance.git
cd voice-ai-governance

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -e ".[dev]"

pytest tests/ -v
```

The `[dev]` extra installs `pytest`, `pytest-cov`, `ruff`, and `mypy`.
Platform dependencies (Pipecat, Twilio, LiveKit, Redis) use lazy imports
and are not required for the core test suite.

---

## 2. Repository structure

```
src/voice_ai_governance/
├── compliance.py          # Core voice compliance policy engine
├── warm_transfer.py       # WarmTransferStateManager (Redis-backed)
├── pii_scrubber.py        # PHI/FERPA PII detection and redaction
├── tcpa.py                # TCPA consent lifecycle and quiet hours
├── confidence.py          # ASR confidence-gated escalation
├── sms.py                 # A2P 10DLC + post-call SMS compliance
├── adapters/              # Platform-specific integration adapters
│   ├── pipecat.py         # Pipecat pipeline integration
│   └── twilio.py          # Twilio ConversationRelay integration
└── audit.py               # Voice session audit log schema
tests/
examples/
```

---

## 3. How to add a new platform adapter

### Step 1 — Open an issue first

Open an issue with label `new-platform-adapter`. Describe the platform,
its hook/callback mechanism, and the minimum SDK version.

### Step 2 — Create the adapter file

Create `src/voice_ai_governance/adapters/<platform>.py`:

```python
"""
<platform>.py — <Platform> adapter for voice AI compliance enforcement.

Lazy import: <platform-package> is imported inside methods only.

Regulatory context:
  TCPA 47 U.S.C. § 227 — consent required for automated calls/texts.
  HIPAA 45 CFR § 164.502 — minimum necessary disclosure for PHI.
  EU AI Act Article 14 — human override capability for voice AI agents.
"""

from __future__ import annotations
from ..compliance import VoiceCompliancePolicy


class <Platform>ComplianceAdapter:
    """
    Wraps VoiceCompliancePolicy as a <Platform> session hook.
    Lazy import: <platform-package> imported inside __init__ or run().
    """

    def __init__(self, policy: VoiceCompliancePolicy) -> None:
        self.policy = policy
```

### Step 3 — Write tests using duck-typed stubs

Tests must not import the optional platform SDK:

```python
class _StubSession:
    def __init__(self): self.events = []
    def on_transcript(self, text): return {"text": text, "confidence": 0.92}
```

### Step 4 — Update ECOSYSTEM.md and exports

Add to `src/voice_ai_governance/adapters/__init__.py`.

---

## 4. How to add a new compliance policy

Create a class implementing the `VoicePolicy` protocol:

```python
from voice_ai_governance.compliance import VoicePolicy, VoiceSessionContext

class CCPAOptOutPolicy(VoicePolicy):
    """
    CCPA § 1798.120 — consumer right to opt out of sale/sharing of
    personal information collected during voice AI interactions.
    """
    def evaluate(self, context: VoiceSessionContext) -> PolicyDecision:
        ...
```

---

## 5. PR checklist

- [ ] `pytest tests/ -v` passes
- [ ] `ruff check src/ tests/` clean
- [ ] `mypy src/` clean
- [ ] Platform/Redis imports are lazy (not at module level)
- [ ] Tests use duck-typed stubs (no optional SDK imports in tests)
- [ ] Regulation citation present in docstring (TCPA § / HIPAA § / FERPA §)
- [ ] CHANGELOG.md updated under `## [Unreleased]`
- [ ] ECOSYSTEM.md updated for new adapter

## Out of scope

- Contributions that suppress AI disclosure in violation of TCPA or EU AI Act
- Hard platform dependencies (all platform imports must be lazy/optional)
- Vendor-specific sales material or proprietary call-center implementations
