"""
Confidence-gated escalation for voice AI pipelines.

Implements threshold-based escalation from autonomous voice agent to human,
with configurable triggers, actions, and audit logging for HIPAA/FERPA compliance.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

__all__ = [
    "EscalationTrigger",
    "EscalationAction",
    "EscalationResult",
    "ConfidenceGatedEscalationPolicy",
]


class EscalationTrigger(str, Enum):
    LOW_CONFIDENCE = "low_confidence"
    INTENT_AMBIGUITY = "intent_ambiguity"
    PII_DETECTED = "pii_detected"
    POLICY_VIOLATION = "policy_violation"
    USER_REQUEST = "user_request"
    TOOL_RISK_HIGH = "tool_risk_high"
    CONSENT_REQUIRED = "consent_required"
    MAX_TURNS_REACHED = "max_turns_reached"


class EscalationAction(str, Enum):
    WARM_TRANSFER = "warm_transfer"
    COLD_TRANSFER = "cold_transfer"
    CALLBACK_SCHEDULE = "callback_schedule"
    RESTRICT_TOOLS = "restrict_tools"
    REQUEST_CLARIFICATION = "request_clarification"
    END_CALL = "end_call"


@dataclass
class EscalationResult:
    triggered: bool
    trigger: Optional[EscalationTrigger] = None
    action: Optional[EscalationAction] = None
    confidence_score: float = 0.0
    threshold_used: float = 0.0
    reason: str = ""
    audit_log_entry: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        self.audit_log_entry = {
            "triggered": self.triggered,
            "trigger": self.trigger.value if self.trigger else None,
            "action": self.action.value if self.action else None,
            "confidence_score": self.confidence_score,
            "threshold_used": self.threshold_used,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }


@dataclass
class EscalationThreshold:
    trigger: EscalationTrigger
    threshold: float  # Escalate when confidence BELOW this value
    action: EscalationAction
    priority: int = 0  # Higher = checked first


class ConfidenceGatedEscalationPolicy:
    """
    Multi-threshold confidence-gated escalation policy for voice AI pipelines.

    Supports composite confidence signals: ASR confidence, LLM confidence,
    tool-call risk score, intent certainty score.

    Complies with OWASP ASI-09 (Human-Agent Trust Exploitation) by ensuring
    autonomous agents do not continue when confidence is insufficient to
    guarantee safe, compliant responses.

    Example:
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
            ],
            on_escalation=my_transfer_handler,
        )
        result = policy.evaluate(confidence_score=0.55, context=turn_context)
    """

    def __init__(
        self,
        thresholds: List[EscalationThreshold],
        on_escalation: Optional[Callable[[EscalationResult], None]] = None,
        audit_logger: Optional[Callable[[Dict[str, Any]], None]] = None,
        default_action: EscalationAction = EscalationAction.WARM_TRANSFER,
    ):
        self.thresholds = sorted(thresholds, key=lambda t: -t.priority)
        self.on_escalation = on_escalation
        self.audit_logger = audit_logger
        self.default_action = default_action
        self._escalation_count = 0
        self._false_positive_count = 0

    def evaluate(
        self,
        confidence_score: float,
        context: Optional[Dict[str, Any]] = None,
        additional_signals: Optional[Dict[str, float]] = None,
    ) -> EscalationResult:
        """
        Evaluate whether to escalate based on confidence score and additional signals.

        Args:
            confidence_score: Primary confidence score [0.0, 1.0]
            context: Conversation context (turn history, intent, entities)
            additional_signals: Optional dict of additional signal scores
                               e.g. {"asr_confidence": 0.8, "tool_risk": 0.3}

        Returns:
            EscalationResult with trigger decision and audit log entry.
        """
        context = context or {}
        additional_signals = additional_signals or {}

        # Composite score: weighted average of all signals
        composite_score = self._compute_composite_score(
            confidence_score, additional_signals
        )

        for threshold_config in self.thresholds:
            if self._check_threshold(
                threshold_config, composite_score, context, additional_signals
            ):
                result = EscalationResult(
                    triggered=True,
                    trigger=threshold_config.trigger,
                    action=threshold_config.action,
                    confidence_score=composite_score,
                    threshold_used=threshold_config.threshold,
                    reason=f"Composite confidence {composite_score:.3f} < threshold {threshold_config.threshold:.3f}",
                )
                self._escalation_count += 1
                self._log_and_callback(result)
                return result

        return EscalationResult(
            triggered=False,
            confidence_score=composite_score,
            threshold_used=min(t.threshold for t in self.thresholds) if self.thresholds else 0.0,
        )

    def _compute_composite_score(
        self,
        primary_score: float,
        additional_signals: Dict[str, float],
    ) -> float:
        if not additional_signals:
            return primary_score
        weights = {"primary": 0.6, "asr_confidence": 0.2, "tool_risk": -0.2}
        score = primary_score * weights.get("primary", 0.6)
        for signal_name, signal_value in additional_signals.items():
            w = weights.get(signal_name, 0.1)
            score += signal_value * w
        return max(0.0, min(1.0, score))

    def _check_threshold(
        self,
        threshold_config: EscalationThreshold,
        composite_score: float,
        context: Dict[str, Any],
        additional_signals: Dict[str, float],
    ) -> bool:
        if threshold_config.trigger == EscalationTrigger.PII_DETECTED:
            return bool(context.get("pii_detected", False))
        if threshold_config.trigger == EscalationTrigger.USER_REQUEST:
            return bool(context.get("user_requested_human", False))
        if threshold_config.trigger == EscalationTrigger.TOOL_RISK_HIGH:
            tool_risk = additional_signals.get("tool_risk", 0.0)
            return tool_risk > threshold_config.threshold
        if threshold_config.trigger == EscalationTrigger.CONSENT_REQUIRED:
            return bool(context.get("consent_required", False) and not context.get("consent_obtained", False))
        if threshold_config.trigger == EscalationTrigger.MAX_TURNS_REACHED:
            max_turns = context.get("max_turns", 20)
            current_turns = context.get("turn_count", 0)
            return current_turns >= max_turns
        # Default: threshold on composite score
        return composite_score < threshold_config.threshold

    def _log_and_callback(self, result: EscalationResult) -> None:
        if self.audit_logger:
            self.audit_logger(result.audit_log_entry)
        if self.on_escalation:
            self.on_escalation(result)

    @property
    def escalation_rate(self) -> float:
        return self._escalation_count

    def reset_metrics(self) -> None:
        self._escalation_count = 0
        self._false_positive_count = 0
