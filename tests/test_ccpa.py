"""Tests for CCPA voice AI compliance module."""
from __future__ import annotations
import pytest
from voice_ai_governance.ccpa import (
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
            record.consumer_id = "other"

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
