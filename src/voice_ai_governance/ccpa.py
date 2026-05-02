"""
CCPA Consumer Rights and Opt-Out Enforcement for Voice AI Agents
================================================================
Regulatory scope:
  Cal. Civil Code §§ 1798.100–1798.199 (CCPA / CPRA)
  §1798.100  — Right to Know
  §1798.105  — Right to Delete (45-day response window)
  §1798.120  — Right to Opt-Out of Sale/Sharing
  §1798.121  — Automated Decision-Making Opt-Out (CPRA)
  §1798.125  — Right to Non-Discrimination
  §1798.135  — Designated methods for submitting requests (toll-free / web form)

Install
-------
    pip install voice-ai-governance

Usage
-----
    from voice_ai_governance.ccpa import CCPAVoicePolicy, CCPARequestTracker

    policy  = CCPAVoicePolicy()
    tracker = CCPARequestTracker()

    context = {
        "consumer_id": "c-001",
        "california_resident": True,
        "consumer_request_type": "right_to_delete",
        "transcript": "Please delete all my information.",
    }

    result = policy.check(context)
    if not result.passed:
        for v in result.violations:
            print(v.rule_id, v.severity, v.description)
        tracker.record_request(context["consumer_id"], "right_to_delete")
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from voice_ai_governance.compliance import (
    ComplianceCheckResult,
    CompliancePolicy,
    ComplianceViolation,
    ViolationSeverity,
)

__all__ = [
    "CCPAVoicePolicy",
    "CCPAOptOutRecord",
    "CCPARequestTracker",
    "CCPA_TRIGGER_PHRASES",
]

# ---------------------------------------------------------------------------
# Trigger phrase catalogue
# Each key is a canonical lowercase phrase; value is the right type it signals.
# Phrase matching uses substring search, so partial matches are intentional.
# ---------------------------------------------------------------------------
CCPA_TRIGGER_PHRASES: dict[str, str] = {
    # §1798.105 — Right to Delete
    "delete my information": "right_to_delete",
    "delete my data": "right_to_delete",
    "remove my data": "right_to_delete",
    "erase my information": "right_to_delete",
    "remove my information": "right_to_delete",
    # §1798.120 — Right to Opt-Out of Sale/Sharing
    "opt out": "right_to_opt_out",
    "don't sell my information": "right_to_opt_out",
    "do not sell my information": "right_to_opt_out",
    "stop sharing my data": "right_to_opt_out",
    "stop selling my data": "right_to_opt_out",
    "don't share my data": "right_to_opt_out",
    # §1798.100 — Right to Know
    "right to know": "right_to_know",
    "what data do you have": "right_to_know",
    "what information do you have": "right_to_know",
    "what information do you collect": "right_to_know",
    "what data have you collected": "right_to_know",
    "what personal information": "right_to_know",
    # §1798.121 — Automated Decision-Making Opt-Out (CPRA)
    "don't use my data for decisions": "opt_out_automated_decision",
    "opt out of automated": "opt_out_automated_decision",
    "stop automated decisions": "opt_out_automated_decision",
    "no automated decision": "opt_out_automated_decision",
    # §1798.125 — Right to Non-Discrimination
    "right to non-discrimination": "right_to_non_discrimination",
    "don't discriminate": "right_to_non_discrimination",
    "treating me differently": "right_to_non_discrimination",
}

# ---------------------------------------------------------------------------
# Rule metadata — maps each right to its statutory citation, severity level,
# and the required_action text that must appear in ComplianceCheckResult.
# CRITICAL severity is used when inaction exposes the operator to statutory
# penalties (§1798.155 up to $7,500/intentional violation); WARNING is used
# for informational enquiries where failure to route creates audit risk but
# does not itself constitute a completed violation.
# ---------------------------------------------------------------------------
_RIGHT_METADATA: dict[str, dict[str, Any]] = {
    "right_to_know": {
        "rule_id": "CCPA-100",
        "citation": "Cal. Civil Code §1798.100",
        "severity": ViolationSeverity.WARNING,
        "description": (
            "Consumer invoked Right to Know. Voice agent cannot fulfil disclosure "
            "inline; request must be routed to designated fulfilment channel."
        ),
        "required_action": (
            "Acknowledge request verbally. Route consumer to human agent or web "
            "form. Provide toll-free number per §1798.135. "
            "Respond within 45 days (§1798.105 timeline applied by analogy)."
        ),
    },
    "right_to_delete": {
        "rule_id": "CCPA-105",
        "citation": "Cal. Civil Code §1798.105",
        "severity": ViolationSeverity.CRITICAL,
        "description": (
            "Consumer invoked Right to Delete. Voice agent must not attempt "
            "inline deletion; must acknowledge and route to fulfilment system."
        ),
        "required_action": (
            "Acknowledge deletion request verbally. Do not process deletion inline. "
            "Route to human agent. Log request with timestamp for 45-day SLA. "
            "Provide toll-free/web confirmation per §1798.135."
        ),
    },
    "right_to_opt_out": {
        "rule_id": "CCPA-120",
        "citation": "Cal. Civil Code §1798.120",
        "severity": ViolationSeverity.CRITICAL,
        "description": (
            "Consumer opted out of sale/sharing of personal information. "
            "Opt-out must be honoured within 15 business days per §1798.120(b)."
        ),
        "required_action": (
            "Acknowledge opt-out request verbally and in writing. "
            "Record opt-out immediately. Do not sell/share data pending confirmation. "
            "Issue written confirmation within 15 business days."
        ),
    },
    "right_to_non_discrimination": {
        "rule_id": "CCPA-125",
        "citation": "Cal. Civil Code §1798.125",
        "severity": ViolationSeverity.CRITICAL,
        "description": (
            "Consumer invoked Right to Non-Discrimination. Any service-level "
            "difference based on rights exercise is prohibited under §1798.125."
        ),
        "required_action": (
            "Do not alter service quality, price, or level in response to consumer "
            "rights exercise. Route concern to compliance officer. "
            "Document incident for audit trail."
        ),
    },
    "opt_out_automated_decision": {
        "rule_id": "CPRA-121",
        "citation": "Cal. Civil Code §1798.121 (CPRA)",
        "severity": ViolationSeverity.CRITICAL,
        "description": (
            "Consumer opted out of automated decision-making that significantly "
            "affects them. Processing must pause pending human review (CPRA §1798.121)."
        ),
        "required_action": (
            "Halt any pending automated decision affecting this consumer. "
            "Acknowledge opt-out verbally. Route to human agent for manual review. "
            "Log opt-out record with timestamp and channel."
        ),
    },
}


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CCPAOptOutRecord:
    """Immutable record of a consumer opt-out event for audit purposes."""

    consumer_id: str
    opt_out_type: str
    timestamp: datetime
    channel: str
    acknowledged: bool


# ---------------------------------------------------------------------------
# Request tracker
# ---------------------------------------------------------------------------


class CCPARequestTracker:
    """
    In-memory store for pending CCPA consumer requests.

    Intended for short-lived, session-scoped tracking. Callers responsible
    for persisting records to durable storage before session ends — this
    class makes no I/O calls.
    """

    def __init__(self) -> None:
        # consumer_id -> list of (request_type, timestamp) tuples
        self._pending: dict[str, list[tuple[str, datetime]]] = defaultdict(list)

    def record_request(self, consumer_id: str, request_type: str) -> None:
        """Append a timestamped request entry for the given consumer."""
        self._pending[consumer_id].append(
            (request_type, datetime.now(tz=timezone.utc))
        )

    def get_pending(self, consumer_id: str) -> list[tuple[str, datetime]]:
        """Return all pending requests for a consumer, oldest first."""
        return list(self._pending.get(consumer_id, []))


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


class CCPAVoicePolicy(CompliancePolicy):
    """
    CCPA / CPRA compliance policy for voice AI agents.

    A ``check()`` call returns ``passed=False`` whenever a CCPA consumer
    right is detected — either via an explicit ``consumer_request_type``
    context key or via transcript phrase matching.  Voice agents cannot
    fulfil consumer rights requests inline; they must acknowledge and route.
    Returning ``passed=False`` signals to the agent runtime that the call
    must not continue without human escalation.

    Context keys consumed
    ---------------------
    consumer_request_type : str, optional
        Explicit right type:
        "right_to_know" | "right_to_delete" | "right_to_opt_out" |
        "right_to_non_discrimination" | "opt_out_automated_decision"
    transcript : str, optional
        Full or partial transcript of the voice interaction. Phrase-matched
        against CCPA_TRIGGER_PHRASES.
    california_resident : bool, optional
        Whether the consumer is a California resident.  Defaults to True
        when absent to avoid erroneously skipping compliance checks for
        unidentified callers.
    consumer_id : str, optional
        Caller identifier for audit log enrichment.
    """

    @property
    def regulation_name(self) -> str:
        return "CCPA/CPRA"

    def check(self, context: dict[str, Any]) -> ComplianceCheckResult:
        # CCPA applies to California residents; default True so that unknown
        # residency is treated conservatively (opt-in to protection).
        if not context.get("california_resident", True):
            return ComplianceCheckResult(
                passed=True,
                violations=[],
                required_actions=[],
                audit_log={
                    "regulation": self.regulation_name,
                    "result": "skipped_non_california_resident",
                    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                },
            )

        detected_right = self._detect_right(context)
        if detected_right is None:
            return ComplianceCheckResult(
                passed=True,
                violations=[],
                required_actions=[],
                audit_log={
                    "regulation": self.regulation_name,
                    "result": "no_consumer_right_detected",
                    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                },
            )

        meta = _RIGHT_METADATA[detected_right]
        timestamp = datetime.now(tz=timezone.utc)

        violation = ComplianceViolation(
            regulation=self.regulation_name,
            rule_id=meta["rule_id"],
            description=meta["description"],
            severity=meta["severity"],
            recommended_action=meta["required_action"],
            timestamp=timestamp,
        )

        audit_log: dict[str, Any] = {
            "regulation": self.regulation_name,
            "rule_id": meta["rule_id"],
            "citation": meta["citation"],
            "right_detected": detected_right,
            "consumer_id": context.get("consumer_id", "unknown"),
            "channel": "voice",
            "timestamp": timestamp.isoformat(),
            "detection_method": (
                "explicit_request_type"
                if context.get("consumer_request_type") == detected_right
                else "transcript_phrase_match"
            ),
        }

        self.on_violation(violation)

        return ComplianceCheckResult(
            passed=False,
            violations=[violation],
            required_actions=[meta["required_action"]],
            audit_log=audit_log,
        )

    def on_violation(self, violation: ComplianceViolation) -> None:
        # Default is a no-op; callers should subclass or monkey-patch to wire
        # in their alerting / logging pipeline.
        pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _detect_right(self, context: dict[str, Any]) -> str | None:
        """
        Return the canonical right type string if a CCPA right is detected,
        otherwise None.

        Explicit ``consumer_request_type`` always takes precedence over
        transcript matching so that upstream intent classifiers can assert
        the right type with confidence without being overridden by noisy
        phrase detection.
        """
        explicit = context.get("consumer_request_type")
        if explicit and explicit in _RIGHT_METADATA:
            return explicit

        transcript = context.get("transcript", "")
        if not transcript:
            return None

        lower = transcript.lower()
        for phrase, right_type in CCPA_TRIGGER_PHRASES.items():
            if phrase in lower:
                return right_type

        return None
