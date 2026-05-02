---
description: How to add a new telephony adapter or compliance policy to voice-ai-governance
---

# Skill: Add a New Telephony Adapter or Voice Compliance Policy

Use this when extending voice-ai-governance with a new telephony platform integration or a new regulatory compliance policy.

## Adding a Telephony Adapter

### Files to create
1. `src/voice_ai_governance/adapters/{platform}_adapter.py` — the adapter
2. `tests/test_{platform}_adapter.py` — adapter tests

### Adapter structure

```python
from __future__ import annotations
from dataclasses import dataclass
from voice_ai_governance.consent import TCPAConsentManager
from voice_ai_governance.phi_scrubber import PHIScrubber
from voice_ai_governance.eu_policy import EUAIActVoicePolicy
from voice_ai_governance.session import VoiceSession, SessionAuditLog

@dataclass(frozen=True)
class {Platform}VoiceAdapter:
    """Voice governance adapter for {Platform} telephony."""
    
    consent_manager: TCPAConsentManager
    phi_scrubber: PHIScrubber
    eu_policy: EUAIActVoicePolicy

    def handle_call(self, call_event: dict) -> SessionAuditLog:
        """Process incoming call through full compliance stack."""
        caller_id = call_event.get("caller_id")
        
        # 1. TCPA consent check
        consent = self.consent_manager.check(caller_id)
        if not consent.is_valid:
            return SessionAuditLog(
                session_id=call_event.get("call_sid"),
                decision="BLOCKED",
                reason="No valid TCPA consent",
                regulation_citation="TCPA 47 U.S.C. § 227",
            )
        
        # 2. EU AI Act disclosure
        self.eu_policy.disclose_ai()
        
        # 3. PHI scrubbing on transcripts
        # ... (platform-specific transcript handling)
        
        return SessionAuditLog(session_id=call_event.get("call_sid"), decision="APPROVED")
```

### Compliance requirements
Every adapter MUST:
- Check TCPA consent before initiating any AI communication (47 U.S.C. § 227)
- Trigger EU AI Act Art. 13 disclosure at session start
- Route transcript through `PHIScrubber` before any LLM processing
- Preserve `WarmTransferState` for handoff to human agent
- Return `SessionAuditLog` for every call event

### Test requirements
Minimum 6 tests per adapter:
1. Consented caller → full session with PHI scrubbing
2. Non-consented caller → blocked before AI interaction
3. PHI detected in transcript → scrubbed before LLM
4. Low ASR confidence → warm transfer triggered
5. EU AI Act disclosure fires at session start
6. Audit log created with all required fields

## Adding a Voice Compliance Policy

### Files to create
1. `src/voice_ai_governance/policies/{regulation}_policy.py`
2. `tests/test_{regulation}_policy.py`

### Policy structure

```python
from __future__ import annotations
from dataclasses import dataclass
from voice_ai_governance.session import PolicyDecision

@dataclass(frozen=True)
class {Regulation}VoicePolicy:
    """Enforce {Regulation} compliance in voice AI sessions."""

    def evaluate(self, session_context: dict) -> PolicyDecision:
        """Return PROCEED, BLOCK, or TRANSFER_TO_HUMAN."""
        raise NotImplementedError
```

## README update (required after every new adapter)

Add to the "Supported Platforms" table:
```
| {Platform} | `{Platform}VoiceAdapter` | TCPA + HIPAA + EU AI Act |
```

## CHANGELOG entry

```markdown
## [vX.Y.Z] — YYYY-MM-DD

### Added — {Platform} Adapter (`{platform}_adapter.py`)

- `{Platform}VoiceAdapter` — TCPA consent + HIPAA PHI scrubbing + EU AI Act for {Platform}
- N new tests. Total: **NN passed**.
```
