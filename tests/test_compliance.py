"""Tests for compliance policies."""

import pytest
from voice_ai_governance.compliance import (
    EUAIActVoicePolicy,
    FERPAVoicePolicy,
    HIPAAVoicePolicy,
    ViolationSeverity,
)


class TestHIPAAVoicePolicy:
    def test_no_violation_clean_context(self):
        policy = HIPAAVoicePolicy()
        result = policy.check({"entities": {"topic": "billing"}})
        assert result.passed

    def test_critical_violation_phi_without_scrub(self):
        policy = HIPAAVoicePolicy(scrub_phi_before_transfer=True)
        result = policy.check({
            "transfer_initiated": True,
            "phi_scrubbed": False,
            "entities": {"ssn": "123-45-6789", "date_of_birth": "01/01/1980"},
        })
        assert not result.passed
        assert any(v.severity == ViolationSeverity.CRITICAL for v in result.violations)
        assert "scrub_phi" in result.required_actions

    def test_no_violation_phi_scrubbed(self):
        policy = HIPAAVoicePolicy()
        result = policy.check({
            "transfer_initiated": True,
            "phi_scrubbed": True,
            "entities": {"topic": "medication"},
        })
        assert result.passed

    def test_recording_without_consent(self):
        policy = HIPAAVoicePolicy(require_consent_for_recording=True)
        result = policy.check({
            "recording_active": True,
            "consent_obtained": False,
        })
        violations = [v for v in result.violations if v.rule_id == "HIPAA-164.522-01"]
        assert len(violations) == 1
        assert "obtain_consent" in result.required_actions

    def test_recording_with_consent_no_violation(self):
        policy = HIPAAVoicePolicy(require_consent_for_recording=True)
        result = policy.check({
            "recording_active": True,
            "consent_obtained": True,
        })
        assert result.passed


class TestFERPAVoicePolicy:
    def test_no_violation_no_education_records(self):
        policy = FERPAVoicePolicy()
        result = policy.check({"entities": {"topic": "general_inquiry"}})
        assert result.passed

    def test_critical_violation_records_without_identity_verification(self):
        policy = FERPAVoicePolicy(require_caller_identity_verification=True)
        result = policy.check({
            "entities": {"gpa": "3.5", "financial_aid_balance": "5000"},
            "caller_identity_verified": False,
        })
        assert not result.passed
        assert any(v.rule_id == "FERPA-99.31-01" for v in result.violations)
        assert "verify_caller_identity" in result.required_actions

    def test_no_violation_with_identity_verified(self):
        policy = FERPAVoicePolicy(require_caller_identity_verification=True)
        result = policy.check({
            "entities": {"gpa": "3.5"},
            "caller_identity_verified": True,
        })
        assert result.passed

    def test_directory_info_opt_out_violation(self):
        policy = FERPAVoicePolicy(directory_info_opt_out=True)
        result = policy.check({
            "entities": {"major": "Computer Science"},
            "caller_is_student": False,
        })
        violations = [v for v in result.violations if v.rule_id == "FERPA-99.37-01"]
        assert len(violations) == 1

    def test_student_accessing_own_directory_info_ok(self):
        policy = FERPAVoicePolicy(directory_info_opt_out=True)
        result = policy.check({
            "entities": {"major": "Computer Science"},
            "caller_is_student": True,
        })
        # Student accessing own directory info is permitted
        assert result.passed


class TestEUAIActVoicePolicy:
    def test_no_violation_ai_disclosed(self):
        policy = EUAIActVoicePolicy(require_ai_disclosure=True)
        result = policy.check({"ai_identity_disclosed": True})
        assert result.passed

    def test_violation_ai_not_disclosed(self):
        policy = EUAIActVoicePolicy(require_ai_disclosure=True)
        result = policy.check({"ai_identity_disclosed": False})
        violations = [v for v in result.violations if v.rule_id == "EUAIA-13.1-01"]
        assert len(violations) == 1
        assert "disclose_ai_identity" in result.required_actions

    def test_critical_violation_high_risk_no_override(self):
        policy = EUAIActVoicePolicy(
            require_ai_disclosure=False,
            is_high_risk_context=True,
            require_human_override_capability=True,
        )
        result = policy.check({"human_override_available": False})
        assert not result.passed
        assert any(v.rule_id == "EUAIA-14.3-01" for v in result.violations)

    def test_no_violation_high_risk_with_override(self):
        policy = EUAIActVoicePolicy(
            require_ai_disclosure=False,
            is_high_risk_context=True,
            require_human_override_capability=True,
        )
        result = policy.check({"human_override_available": True})
        assert result.passed
