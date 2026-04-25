"""Tests for confidence-gated escalation policy."""

import pytest
from voice_ai_governance.escalation import (
    ConfidenceGatedEscalationPolicy,
    EscalationAction,
    EscalationThreshold,
    EscalationTrigger,
)


@pytest.fixture
def basic_policy():
    return ConfidenceGatedEscalationPolicy(
        thresholds=[
            EscalationThreshold(
                trigger=EscalationTrigger.LOW_CONFIDENCE,
                threshold=0.65,
                action=EscalationAction.WARM_TRANSFER,
                priority=10,
            ),
            EscalationThreshold(
                trigger=EscalationTrigger.PII_DETECTED,
                threshold=1.0,
                action=EscalationAction.WARM_TRANSFER,
                priority=20,
            ),
        ]
    )


class TestConfidenceGatedEscalationPolicy:
    def test_no_escalation_above_threshold(self, basic_policy):
        result = basic_policy.evaluate(confidence_score=0.90)
        assert not result.triggered

    def test_escalation_below_threshold(self, basic_policy):
        result = basic_policy.evaluate(confidence_score=0.50)
        assert result.triggered
        assert result.trigger == EscalationTrigger.LOW_CONFIDENCE
        assert result.action == EscalationAction.WARM_TRANSFER

    def test_escalation_at_boundary(self, basic_policy):
        # At exactly the threshold, should NOT escalate (strict <)
        result = basic_policy.evaluate(confidence_score=0.65)
        assert not result.triggered

    def test_pii_trigger_overrides_confidence(self, basic_policy):
        # Even with high confidence, PII detected triggers escalation
        result = basic_policy.evaluate(
            confidence_score=0.95,
            context={"pii_detected": True},
        )
        assert result.triggered
        assert result.trigger == EscalationTrigger.PII_DETECTED

    def test_user_request_trigger(self):
        policy = ConfidenceGatedEscalationPolicy(
            thresholds=[
                EscalationThreshold(
                    trigger=EscalationTrigger.USER_REQUEST,
                    threshold=1.0,
                    action=EscalationAction.WARM_TRANSFER,
                    priority=30,
                )
            ]
        )
        result = policy.evaluate(
            confidence_score=0.95,
            context={"user_requested_human": True},
        )
        assert result.triggered
        assert result.trigger == EscalationTrigger.USER_REQUEST

    def test_audit_log_populated_on_escalation(self, basic_policy):
        result = basic_policy.evaluate(confidence_score=0.40)
        assert result.triggered
        assert "triggered" in result.audit_log_entry
        assert result.audit_log_entry["triggered"] is True
        assert result.audit_log_entry["confidence_score"] == pytest.approx(0.40, abs=0.05)

    def test_callback_invoked_on_escalation(self):
        callback_results = []
        policy = ConfidenceGatedEscalationPolicy(
            thresholds=[
                EscalationThreshold(
                    trigger=EscalationTrigger.LOW_CONFIDENCE,
                    threshold=0.7,
                    action=EscalationAction.WARM_TRANSFER,
                )
            ],
            on_escalation=callback_results.append,
        )
        policy.evaluate(confidence_score=0.50)
        assert len(callback_results) == 1
        assert callback_results[0].triggered

    def test_no_thresholds_no_escalation(self):
        policy = ConfidenceGatedEscalationPolicy(thresholds=[])
        result = policy.evaluate(confidence_score=0.0)
        assert not result.triggered

    def test_max_turns_trigger(self):
        policy = ConfidenceGatedEscalationPolicy(
            thresholds=[
                EscalationThreshold(
                    trigger=EscalationTrigger.MAX_TURNS_REACHED,
                    threshold=0.0,
                    action=EscalationAction.WARM_TRANSFER,
                )
            ]
        )
        result = policy.evaluate(
            confidence_score=0.9,
            context={"turn_count": 25, "max_turns": 20},
        )
        assert result.triggered

    def test_priority_ordering(self):
        """Higher priority threshold should trigger before lower priority."""
        triggered_triggers = []

        def callback(r):
            triggered_triggers.append(r.trigger)

        policy = ConfidenceGatedEscalationPolicy(
            thresholds=[
                EscalationThreshold(
                    trigger=EscalationTrigger.LOW_CONFIDENCE,
                    threshold=0.9,
                    action=EscalationAction.WARM_TRANSFER,
                    priority=5,  # Lower priority
                ),
                EscalationThreshold(
                    trigger=EscalationTrigger.PII_DETECTED,
                    threshold=1.0,
                    action=EscalationAction.WARM_TRANSFER,
                    priority=20,  # Higher priority
                ),
            ],
            on_escalation=callback,
        )
        result = policy.evaluate(
            confidence_score=0.5,
            context={"pii_detected": True},
        )
        # PII_DETECTED (priority 20) should win over LOW_CONFIDENCE (priority 5)
        assert result.trigger == EscalationTrigger.PII_DETECTED

    def test_escalation_count_tracking(self, basic_policy):
        basic_policy.evaluate(confidence_score=0.3)
        basic_policy.evaluate(confidence_score=0.4)
        basic_policy.evaluate(confidence_score=0.9)  # No escalation
        assert basic_policy.escalation_rate == 2
