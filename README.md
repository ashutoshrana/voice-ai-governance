# voice-ai-governance

**Compliance enforcement middleware for voice and SMS AI pipelines.**

[![PyPI version](https://badge.fury.io/py/voice-ai-governance.svg)](https://badge.fury.io/py/voice-ai-governance)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Coverage](https://codecov.io/gh/ashutoshrana/voice-ai-governance/branch/main/graph/badge.svg)](https://codecov.io/gh/ashutoshrana/voice-ai-governance)
[![CI](https://github.com/ashutoshrana/voice-ai-governance/actions/workflows/ci.yml/badge.svg)](https://github.com/ashutoshrana/voice-ai-governance/actions/workflows/ci.yml)

Warm transfer state management, confidence-gated escalation, PII scrubbing, TCPA/A2P 10DLC SMS compliance, and HIPAA/FERPA/EU AI Act enforcement for voice and SMS AI systems built on **Pipecat**, **LiveKit**, **Twilio ConversationRelay**, **Twilio Programmable Messaging**, and any WebRTC-based voice AI platform.

---

## The Problem

80% of enterprises plan to deploy voice AI by 2026. Yet **only 7% of contact centers** deliver seamless context-preserving handoffs, and **no voice AI framework ships with regulated-industry compliance enforcement**.

When a voice agent escalates a call in a healthcare or higher education context, three things must happen atomically:
1. **PII must be scrubbed** from the transfer payload (HIPAA §164.514, FERPA §99.31)
2. **State must be preserved** without race conditions across concurrent WebSocket + TTS + queue I/O
3. **Confidence must gate the transfer** — too early wastes human time; too late breaks trust

`voice-ai-governance` solves all three.

---

## Features

- **Confidence-gated escalation** — multi-signal threshold policies (logprob + ASR confidence + tool risk + turn count)
- **Lossless warm transfer state** — progressive profiling payload with intent history, entity values, sentiment trajectory
- **PII scrubbing** — regex-based HIPAA PHI, FERPA education record, and SMS URL-parameter PHI detection + redaction
- **TCPA compliance** — quiet hours enforcement, Prior Express Written Consent (PEWC) gating, opt-out interception
- **A2P 10DLC validation** — TCR campaign config validation, AI-generated SMS disclosure enforcement, MIXED type warnings
- **Omnichannel consent bridging** — voice consent records propagate to SMS channel without second opt-in
- **Post-call SMS summaries** — PHI-stripped case summary sent to human agent after escalation
- **Compliance enforcement** — HIPAA, FERPA, EU AI Act Article 13/14 runtime policy checking
- **Redis-gated state** — atomic state management for concurrent telephony I/O (prevents race conditions)
- **Twilio ConversationRelay** — native handoffData payload builder for Flex TaskRouter
- **Pipecat adapter** — drop-in middleware for Pipecat frame processors
- **No required dependencies** — core library has zero required dependencies; Redis and Pipecat are optional

---

## Quick Start

### Installation

```bash
pip install voice-ai-governance
# With Redis support:
pip install "voice-ai-governance[redis]"
# With Pipecat integration:
pip install "voice-ai-governance[pipecat]"
```

### Confidence-Gated Escalation

```python
from voice_ai_governance import (
    ConfidenceGatedEscalationPolicy,
    EscalationThreshold,
    EscalationTrigger,
    EscalationAction,
)

policy = ConfidenceGatedEscalationPolicy(
    thresholds=[
        EscalationThreshold(
            trigger=EscalationTrigger.LOW_CONFIDENCE,
            threshold=0.65,
            action=EscalationAction.WARM_TRANSFER,
            priority=10,
        ),
        EscalationThreshold(
            trigger=EscalationTrigger.PII_DETECTED,
            threshold=1.0,  # Always escalate on PII detection
            action=EscalationAction.WARM_TRANSFER,
            priority=20,
        ),
        EscalationThreshold(
            trigger=EscalationTrigger.USER_REQUEST,
            threshold=1.0,
            action=EscalationAction.WARM_TRANSFER,
            priority=30,
        ),
    ],
)

result = policy.evaluate(
    confidence_score=0.45,
    context={"turn_count": 8, "intent": "billing_inquiry"},
)

if result.triggered:
    print(f"Escalating: {result.trigger} → {result.action}")
    # Initiate warm transfer
```

### Warm Transfer State Management

```python
from voice_ai_governance import WarmTransferStateManager

# In-memory (testing) — swap for Redis in production
manager = WarmTransferStateManager()

# Create session at call start
session_id = manager.create_session(call_sid="CA1234567890")

# Update state each turn
manager.update_state(
    session_id,
    lambda s: s.add_turn(
        role="user",
        utterance="I need help with my invoice",
        intent="billing_inquiry",
        confidence=0.88,
        entities_detected={"invoice_number": "INV-2026-001"},
        sentiment_score=0.3,
    ),
)

# Build handoff payload — PII scrubbed by default
payload = manager.build_handoff_payload(session_id, reason="low_confidence")
print(payload.caller_summary)
# "Caller interaction (3 turns). Intent: billing_inquiry. Sentiment: positive..."
```

### Twilio ConversationRelay Warm Transfer

```python
import json
from voice_ai_governance import WarmTransferStateManager
from voice_ai_governance.adapters.twilio import TwilioWarmTransferAdapter

manager = WarmTransferStateManager(redis_client=redis_client)
adapter = TwilioWarmTransferAdapter(
    state_manager=manager,
    queue_sid="WQ_YOUR_QUEUE_SID",
)

# In your WebSocket handler when escalation triggers:
message = adapter.build_end_session_message(session_id=session_id)
await websocket.send_text(json.dumps(message))
# Sends: {"type": "end", "handoffData": {...contextual data...}}
```

### HIPAA + FERPA Compliance Enforcement

```python
from voice_ai_governance import HIPAAVoicePolicy, FERPAVoicePolicy
from voice_ai_governance.adapters.pipecat import PipecatGovernanceAdapter

adapter = PipecatGovernanceAdapter(
    compliance_policies=[
        HIPAAVoicePolicy(
            require_consent_for_recording=True,
            scrub_phi_before_transfer=True,
        ),
        FERPAVoicePolicy(
            require_caller_identity_verification=True,
        ),
    ],
)

# Check compliance before responding
context = {
    "entities": {"student_id": "STU123", "gpa": "3.8"},
    "caller_identity_verified": False,
}
result = adapter.check_compliance(context)

if not result.passed:
    for violation in result.violations:
        print(f"[{violation.regulation}] {violation.rule_id}: {violation.description}")
    # Trigger escalation or restrict response
```

### EU AI Act Article 13/14 Enforcement

```python
from voice_ai_governance import EUAIActVoicePolicy

policy = EUAIActVoicePolicy(
    require_ai_disclosure=True,   # Article 13.1
    is_high_risk_context=True,    # Annex III: education/employment context
    require_human_override_capability=True,  # Article 14.3
)

result = policy.check({
    "ai_identity_disclosed": True,
    "human_override_available": True,
})
assert result.passed
```

### TCPA-Compliant Outbound SMS

```python
from voice_ai_governance import (
    OmnichannelConsentStore,
    ConsentType,
    A2PCampaignConfig,
    A2PCampaignType,
)
from voice_ai_governance.adapters.twilio_sms import TwilioSMSAdapter

# Record consent captured during voice call
consent_store = OmnichannelConsentStore()
consent_store.record_consent(
    phone="+15551234567",
    consent_type=ConsentType.PRIOR_EXPRESS_WRITTEN,
    channel="voice",
    campaign_id="C1234",
)

adapter = TwilioSMSAdapter(
    from_number="+18005550100",
    consent_store=consent_store,
    campaign_config=A2PCampaignConfig(
        campaign_id="C1234",
        brand_name="Acme Health",
        campaign_type=A2PCampaignType.HEALTHCARE,
        use_case_description="AI appointment reminders for patients",
        ai_generated=True,
        opt_out_message="Reply STOP to unsubscribe. Msg&data rates may apply.",
    ),
)

message = adapter.build_message(
    to="+15551234567",
    body="Your appointment is confirmed. Details: https://portal.example.com/appt?patient_id=9876",
    message_type="transactional",
    recipient_hour=10,  # Checked against TCPA quiet hours (8am–9pm)
)

if message.compliance_passed:
    # twilio_client.messages.create(**message.to_twilio_params())
    print(message.body)
    # "Your appointment is confirmed. Details: https://portal.example.com/appt?patient_id=[REDACTED]"
```

### Post-Call SMS Case Summary

```python
from voice_ai_governance.adapters.twilio_sms import PostCallSMSBuilder

builder = PostCallSMSBuilder(
    portal_base_url="https://portal.example.com/cases",
    scrub_phi=True,
)

message = builder.build(
    to_agent="+15559999999",
    from_number="+18005550100",
    caller_number="+15554445678",
    session_id="sess_abc123",
    intent="billing_inquiry",
    turn_count=5,
    escalation_reason="low_confidence",
)
# Body: "[CASE ALERT] New inbound case\nCaller: ...5678\nIntent: Billing Inquiry | Turns: 5\n..."
# All PHI stripped; portal link appended for secure detail access
```

### Inbound Opt-Out Interception

```python
from voice_ai_governance.adapters.twilio_sms import TwilioSMSAdapter
from voice_ai_governance import OmnichannelConsentStore

store = OmnichannelConsentStore()
adapter = TwilioSMSAdapter(from_number="+18005550100", consent_store=store)

# In your Twilio webhook handler:
result = adapter.process_inbound(from_number="+15551234567", body="STOP")
if result.is_opt_out:
    # opt-out recorded in store — future messages suppressed automatically
    pass  # send CTIA-required confirmation reply
```

---

## Regulations Covered

| Regulation | Coverage |
|-----------|----------|
| HIPAA §164.514 | PHI detection and scrubbing before transfer; URL parameter PHI redaction in SMS |
| HIPAA §164.522 | Consent tracking for voice recording |
| FERPA §99.31 | Education record access restriction |
| FERPA §99.37 | Directory information opt-out enforcement |
| TCPA 47 U.S.C. §227 | Quiet hours (8am–9pm), PEWC for AI marketing SMS, opt-out suppression |
| FCC 2024 TCPA Order | One-to-one consent rule; per-campaign consent granularity |
| CTIA Best Practices | STOP/HELP/UNSUBSCRIBE/CANCEL/END/QUIT keyword interception |
| A2P 10DLC (TCR) | Campaign config validation, AI-generated disclosure, MIXED type throughput warnings |
| EU AI Act Article 13.1 | AI identity disclosure |
| EU AI Act Article 14.3 | Human oversight capability (full obligations August 2026) |
| EU AI Act Article 12 | Logging and monitoring |
| OWASP ASI-02 | Tool misuse prevention via compliance-gated execution |
| OWASP ASI-09 | Human-agent trust exploitation prevention |

---

## Platform Compatibility

| Platform | Adapter | Status |
|---------|---------|--------|
| Pipecat (pipecat-ai) | `PipecatGovernanceAdapter` | ✅ Supported |
| Twilio ConversationRelay | `TwilioWarmTransferAdapter` | ✅ Supported |
| Twilio Programmable Messaging | `TwilioSMSAdapter`, `PostCallSMSBuilder` | ✅ Supported |
| LiveKit Agents | Coming in v0.3 | 🔜 Planned |
| Amazon Connect | Coming in v0.3 | 🔜 Planned |
| Cisco Webex CC | Coming in v0.3 | 🔜 Planned |
| NICE CXone | Coming in v0.4 | 🔜 Planned |

---

## Related Packages

- [regulated-ai-governance](https://github.com/ashutoshrana/regulated-ai-governance) — Runtime tool authorization and capability scoping for agent frameworks
- [enterprise-rag-patterns](https://github.com/ashutoshrana/enterprise-rag-patterns) — FERPA/HIPAA/GDPR-compliant RAG patterns
- [integration-automation-patterns](https://github.com/ashutoshrana/integration-automation-patterns) — EDA patterns for AI agents

---

## License

MIT License. See [LICENSE](LICENSE).
