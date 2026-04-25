"""
Pipecat governance adapter for voice-ai-governance.

Integrates compliance enforcement, PII scrubbing, and warm transfer
state management into Pipecat (pipecat-ai) voice AI pipelines.

Pipecat has 4,200+ GitHub stars and has zero compliance enforcement —
this adapter fills that gap for enterprise deployments in regulated industries.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from voice_ai_governance.compliance import ComplianceCheckResult, CompliancePolicy
from voice_ai_governance.escalation import (
    ConfidenceGatedEscalationPolicy,
    EscalationResult,
)
from voice_ai_governance.pii import PIIScrubber
from voice_ai_governance.state import WarmTransferStateManager


class PipecatGovernanceAdapter:
    """
    Governance middleware adapter for Pipecat voice AI pipelines.

    Wraps Pipecat frame processors with:
    - Pre-turn compliance checking
    - PII scrubbing on transcript frames
    - Confidence-gated escalation evaluation
    - Warm transfer state synchronization

    Example:
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.frames.frames import TranscriptionFrame

        adapter = PipecatGovernanceAdapter(
            compliance_policies=[HIPAAVoicePolicy(), EUAIActVoicePolicy()],
            escalation_policy=ConfidenceGatedEscalationPolicy(thresholds=[...]),
            state_manager=WarmTransferStateManager(redis_client=r),
        )

        # Wrap your pipeline
        @adapter.on_frame(TranscriptionFrame)
        async def process_transcript(frame, state):
            result = adapter.check_compliance(frame.text, state)
            if not result.passed:
                await adapter.initiate_warm_transfer(state.session_id, result)

    Note: Requires pipecat-ai>=0.0.46 installed separately.
    """

    def __init__(
        self,
        compliance_policies: Optional[List[CompliancePolicy]] = None,
        escalation_policy: Optional[ConfidenceGatedEscalationPolicy] = None,
        state_manager: Optional[WarmTransferStateManager] = None,
        pii_scrubber: Optional[PIIScrubber] = None,
        on_warm_transfer: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        self.compliance_policies = compliance_policies or []
        self.escalation_policy = escalation_policy
        self.state_manager = state_manager or WarmTransferStateManager()
        self.pii_scrubber = pii_scrubber or PIIScrubber()
        self.on_warm_transfer = on_warm_transfer

    def check_compliance(
        self,
        context: Dict[str, Any],
    ) -> ComplianceCheckResult:
        """Run all compliance policies against the current context."""
        all_violations = []
        all_actions = []
        all_audit = {}

        for policy in self.compliance_policies:
            result = policy.check(context)
            all_violations.extend(result.violations)
            all_actions.extend(result.required_actions)
            all_audit.update(result.audit_log)
            for v in result.violations:
                policy.on_violation(v)

        passed = not any(True for v in all_violations if v.severity.value == "critical")
        from voice_ai_governance.compliance import ComplianceCheckResult
        return ComplianceCheckResult(
            passed=passed,
            violations=all_violations,
            required_actions=list(set(all_actions)),
            audit_log=all_audit,
        )

    def evaluate_escalation(
        self,
        confidence_score: float,
        context: Dict[str, Any],
        additional_signals: Optional[Dict[str, float]] = None,
    ) -> Optional[EscalationResult]:
        """Evaluate confidence-gated escalation. Returns result if escalation triggered."""
        if not self.escalation_policy:
            return None
        result = self.escalation_policy.evaluate(
            confidence_score, context, additional_signals
        )
        return result if result.triggered else None

    def scrub_transcript(self, transcript: str) -> str:
        """Scrub PII from a voice transcript before logging or transfer."""
        return self.pii_scrubber.scrub_text(transcript).scrubbed_text

    def build_transfer_context(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Build Pipecat-compatible warm transfer context dictionary."""
        payload = self.state_manager.build_handoff_payload(
            session_id, reason="governance_escalation", scrub_pii=True
        )
        if not payload:
            return None
        return payload.to_dict()
