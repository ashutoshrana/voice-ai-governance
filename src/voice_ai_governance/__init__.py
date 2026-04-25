"""
voice-ai-governance: Compliance enforcement middleware for voice AI pipelines.

Provides warm transfer state management, confidence-gated escalation,
PII scrubbing, and regulated-industry compliance (HIPAA, FERPA, EU AI Act)
for voice AI systems built on Pipecat, LiveKit, Twilio, and other platforms.

OWASP Agentic AI Top 10 mitigations: ASI-02 (Tool Misuse), ASI-03
(Identity and Privilege Abuse), ASI-09 (Human-Agent Trust Exploitation).
"""

from voice_ai_governance.escalation import (
    ConfidenceGatedEscalationPolicy,
    EscalationTrigger,
    EscalationAction,
    EscalationResult,
)
from voice_ai_governance.state import (
    WarmTransferStateManager,
    ConversationState,
    HandoffPayload,
    TransferStatus,
)
from voice_ai_governance.pii import (
    PIIScrubber,
    PIIPattern,
    ScrubResult,
)
from voice_ai_governance.compliance import (
    CompliancePolicy,
    HIPAAVoicePolicy,
    FERPAVoicePolicy,
    EUAIActVoicePolicy,
    ComplianceViolation,
)
from voice_ai_governance.adapters.pipecat import PipecatGovernanceAdapter
from voice_ai_governance.adapters.twilio import TwilioWarmTransferAdapter

__version__ = "0.1.0"
__all__ = [
    # Escalation
    "ConfidenceGatedEscalationPolicy",
    "EscalationTrigger",
    "EscalationAction",
    "EscalationResult",
    # State management
    "WarmTransferStateManager",
    "ConversationState",
    "HandoffPayload",
    "TransferStatus",
    # PII
    "PIIScrubber",
    "PIIPattern",
    "ScrubResult",
    # Compliance
    "CompliancePolicy",
    "HIPAAVoicePolicy",
    "FERPAVoicePolicy",
    "EUAIActVoicePolicy",
    "ComplianceViolation",
    # Adapters
    "PipecatGovernanceAdapter",
    "TwilioWarmTransferAdapter",
]
