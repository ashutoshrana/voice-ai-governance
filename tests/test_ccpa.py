"""Tests for CCPA voice AI compliance module."""
from __future__ import annotations
import pytest
from unittest.mock import MagicMock
import sys
import time

for mod in [
    "voice_ai_governance", "voice_ai_governance.compliance",
    "voice_ai_governance.state", "voice_ai_governance.pii",
]:
    if mod not in sys.modules:
        # Create realistic stubs
        m = MagicMock()
        sys.modules[mod] = m

# Override with real-looking stubs for compliance base classes
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List

class ViolationSeverity(str, Enum):
    WARNING = "warning"
    VIOLATION = "violation"
    CRITICAL = "critical"

@dataclass
class ComplianceViolation:
    regulation: str
    rule_id: str
    description: str
    severity: ViolationSeverity
    recommended_action: str
    timestamp: float = field(default_factory=time.time)

@dataclass
class ComplianceCheckResult:
    passed: bool
    violations: List[Any] = field(default_factory=list)
    required_actions: List[str] = field(default_factory=list)
    audit_log: Dict[str, Any] = field(default_factory=dict)

import abc

class CompliancePolicy(abc.ABC):
    @property
    @abc.abstractmethod
    def regulation_name(self) -> str: ...
    @abc.abstractmethod
    def check(self, context: Dict[str, Any]) -> ComplianceCheckResult: ...
    @abc.abstractmethod
    def on_violation(self, violation: Any) -> None: ...

# Inject real stubs
compliance_mod = sys.modules["voice_ai_governance.compliance"]
compliance_mod.CompliancePolicy = CompliancePolicy
compliance_mod.ComplianceCheckResult = ComplianceCheckResult
compliance_mod.ComplianceViolation = ComplianceViolation
compliance_mod.ViolationSeverity = ViolationSeverity

sys.path.insert(0, "/tmp/devbuild")
from ccpa_module import (
    CCPAVoicePolicy,
    CCPAOptOutRecord,
    CCPARequestTracker,
    CCPA_TRIGGER_PHRASES,
)


class TestCCPATriggerPhrases:
    def test_has_opt_out_phrase(self):
        found = any("opt out" in k or "opt_out" in k for k in CCPA_TRIGGER_PHRASES)
        assert found

    def test_has_delete_phrase(self):
        found = any("delete" in k or "remove" in k for k in CCPA_TRIGGER_PHRASES)
        assert found

    def test_has_right_to_know_phrase(self):
        values = list(CCPA_TRIGGER_PHRASES.values())
        assert any("right_to_know" in str(v) or "know" in str(v).lower() for v in values)


class TestCCPAVoicePolicyCheck:
    def setup_method(self):
        self.policy = CCPAVoicePolicy()

    def test_non_california_resident_passes(self):
        result = self.policy.check({"california_resident": False})
        assert result.passed is True

    def test_explicit_opt_out_request_fails(self):
        result = self.policy.check({
            "california_resident": True,
            "consumer_request_type": "right_to_opt_out",
        })
        assert result.passed is False
        assert len(result.violations) >= 1

    def test_delete_request_fails(self):
        result = self.policy.check({
            "california_resident": True,
            "consumer_request_type": "right_to_delete",
        })
        assert result.passed is False

    def test_transcript_opt_out_phrase_detected(self):
        result = self.policy.check({
            "california_resident": True,
            "transcript": "I want to opt out of data sharing",
        })
        assert result.passed is False

    def test_transcript_delete_phrase_detected(self):
        result = self.policy.check({
            "california_resident": True,
            "transcript": "Please delete my information from your system",
        })
        assert result.passed is False

    def test_neutral_transcript_passes(self):
        result = self.policy.check({
            "california_resident": True,
            "transcript": "I need help with my account balance please",
        })
        assert result.passed is True

    def test_violations_have_regulation_field(self):
        result = self.policy.check({
            "california_resident": True,
            "consumer_request_type": "right_to_delete",
        })
        for v in result.violations:
            assert hasattr(v, "regulation") or isinstance(v, dict)

    def test_audit_log_populated(self):
        result = self.policy.check({
            "california_resident": True,
            "consumer_request_type": "right_to_delete",
        })
        assert isinstance(result.audit_log, dict)
        assert len(result.audit_log) > 0

    def test_required_actions_populated(self):
        result = self.policy.check({
            "california_resident": True,
            "consumer_request_type": "right_to_opt_out",
        })
        assert len(result.required_actions) >= 1

    def test_regulation_name_is_ccpa(self):
        assert "CCPA" in self.policy.regulation_name or "ccpa" in self.policy.regulation_name.lower()

    def test_unknown_residency_treated_as_protected(self):
        result = self.policy.check({
            "consumer_request_type": "right_to_delete",
        })
        assert result.passed is False


class TestCCPAOptOutRecord:
    def test_frozen(self):
        record = CCPAOptOutRecord(
            consumer_id="c_001",
            opt_out_type="right_to_opt_out",
            timestamp=1714500000.0,
            channel="voice",
            acknowledged=False,
        )
        with pytest.raises((AttributeError, TypeError)):
            record.consumer_id = "other"  # frozen dataclass

    def test_fields(self):
        record = CCPAOptOutRecord("c1", "right_to_delete", 1714500000.0, "voice", True)
        assert record.consumer_id == "c1"
        assert record.acknowledged is True


class TestCCPARequestTracker:
    def test_record_and_retrieve(self):
        tracker = CCPARequestTracker()
        tracker.record_request("consumer_001", "right_to_opt_out")
        pending = tracker.get_pending("consumer_001")
        assert len(pending) >= 1

    def test_empty_consumer_has_no_pending(self):
        tracker = CCPARequestTracker()
        assert tracker.get_pending("unknown_consumer") == [] or len(tracker.get_pending("unknown_consumer")) == 0

    def test_multiple_requests_same_consumer(self):
        tracker = CCPARequestTracker()
        tracker.record_request("c1", "right_to_know")
        tracker.record_request("c1", "right_to_delete")
        pending = tracker.get_pending("c1")
        assert len(pending) >= 2
