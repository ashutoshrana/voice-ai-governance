"""Tests for SMS compliance modules."""

import pytest
from voice_ai_governance.sms import (
    A2PCampaignConfig,
    A2PCampaignType,
    A2PCampaignValidator,
    ConsentType,
    OmnichannelConsentStore,
    OptOutHandler,
    SMSConsentRecord,
    TCPAPolicy,
)
from voice_ai_governance.pii import SMSPIIScrubber
from voice_ai_governance.adapters.twilio_sms import PostCallSMSBuilder, TwilioSMSAdapter


class TestTCPAPolicy:
    def test_quiet_hours_violation(self):
        policy = TCPAPolicy(enforce_quiet_hours=True)
        result = policy.check(recipient_hour=22)  # 10pm — after 9pm cutoff
        assert not result.passed
        assert any("QUIET-HOURS" in v for v in result.violations)
        assert "delay_until_quiet_hours_end" in result.required_actions

    def test_quiet_hours_early_morning(self):
        policy = TCPAPolicy(enforce_quiet_hours=True)
        result = policy.check(recipient_hour=7)  # 7am — before 8am
        assert not result.passed

    def test_allowed_hours_pass(self):
        policy = TCPAPolicy(enforce_quiet_hours=True, require_consent_for_transactional=False)
        result = policy.check(recipient_hour=10)  # 10am — allowed
        assert result.passed

    def test_no_consent_marketing_violation(self):
        policy = TCPAPolicy(require_pewc_for_marketing=True)
        result = policy.check(message_type="marketing", consent=None)
        assert not result.passed
        assert any("PEWC" in v for v in result.violations)
        assert "obtain_pewc_before_sending" in result.required_actions

    def test_opted_out_suppressed(self):
        policy = TCPAPolicy()
        consent = SMSConsentRecord(
            phone_number="+15551234567",
            consent_type=ConsentType.PRIOR_EXPRESS_WRITTEN,
            timestamp="2026-01-01T10:00:00",
            opted_out=True,
        )
        result = policy.check(consent=consent)
        assert not result.passed
        assert "suppress_message" in result.required_actions

    def test_valid_pewc_marketing_passes(self):
        policy = TCPAPolicy(require_pewc_for_marketing=True)
        consent = SMSConsentRecord(
            phone_number="+15551234567",
            consent_type=ConsentType.PRIOR_EXPRESS_WRITTEN,
            timestamp="2026-01-01T10:00:00",
            opted_out=False,
        )
        result = policy.check(message_type="marketing", recipient_hour=10, consent=consent)
        assert result.passed

    def test_transactional_no_consent_violation(self):
        policy = TCPAPolicy(require_consent_for_transactional=True)
        result = policy.check(message_type="transactional", consent=None)
        assert not result.passed


class TestA2PCampaignValidator:
    def test_missing_opt_out_message(self):
        config = A2PCampaignConfig(
            campaign_id="C1",
            brand_name="Test",
            campaign_type=A2PCampaignType.HEALTHCARE,
            use_case_description="AI appointment reminder",
            opt_out_message="",
        )
        validator = A2PCampaignValidator()
        result = validator.validate(config)
        assert not result.passed
        assert "add_opt_out_message" in result.required_actions

    def test_ai_not_in_description(self):
        config = A2PCampaignConfig(
            campaign_id="C1",
            brand_name="Test",
            campaign_type=A2PCampaignType.HEALTHCARE,
            use_case_description="appointment reminders",  # no "AI"
            ai_generated=True,
            opt_out_message="Reply STOP to unsubscribe.",
        )
        validator = A2PCampaignValidator()
        result = validator.validate(config)
        assert not result.passed
        assert "add_ai_disclosure_to_description" in result.required_actions

    def test_valid_config_passes(self):
        config = A2PCampaignConfig(
            campaign_id="C1",
            brand_name="Acme Health",
            campaign_type=A2PCampaignType.HEALTHCARE,
            use_case_description="AI-generated appointment reminders for patients",
            ai_generated=True,
            opt_out_message="Reply STOP to unsubscribe. Msg&data rates may apply.",
            regulated_content=True,
        )
        validator = A2PCampaignValidator()
        result = validator.validate(config)
        assert result.passed

    def test_mixed_campaign_type_warned(self):
        config = A2PCampaignConfig(
            campaign_id="C2",
            brand_name="Test",
            campaign_type=A2PCampaignType.MIXED,
            use_case_description="AI notifications",
            opt_out_message="Reply STOP to unsubscribe.",
        )
        validator = A2PCampaignValidator()
        result = validator.validate(config)
        assert not result.passed
        assert "change_to_dedicated_campaign_type" in result.required_actions


class TestOptOutHandler:
    def test_stop_keyword_detected(self):
        handler = OptOutHandler()
        result = handler.process("STOP")
        assert result.is_opt_out
        assert "record_opt_out" in result.required_actions

    def test_unsubscribe_keyword_detected(self):
        handler = OptOutHandler()
        result = handler.process("UNSUBSCRIBE")
        assert result.is_opt_out

    def test_help_keyword_detected(self):
        handler = OptOutHandler()
        result = handler.process("HELP")
        assert result.is_help_request
        assert not result.is_opt_out

    def test_normal_message_passes(self):
        handler = OptOutHandler()
        result = handler.process("I need help with my account")
        assert not result.is_opt_out
        assert not result.is_help_request
        assert result.passed

    def test_case_insensitive_stop(self):
        handler = OptOutHandler()
        result = handler.process("stop")
        assert result.is_opt_out


class TestOmnichannelConsentStore:
    def test_record_and_retrieve_consent(self):
        store = OmnichannelConsentStore()
        store.record_consent("+15551234567", ConsentType.PRIOR_EXPRESS_WRITTEN, channel="voice")
        record = store.get_consent("+15551234567")
        assert record is not None
        assert record.consent_type == ConsentType.PRIOR_EXPRESS_WRITTEN
        assert record.channel == "voice"

    def test_opt_out_recorded(self):
        store = OmnichannelConsentStore()
        store.record_consent("+15551234567", ConsentType.PRIOR_EXPRESS_WRITTEN)
        store.record_opt_out("+15551234567")
        assert store.is_opted_out("+15551234567")

    def test_no_consent_returns_none(self):
        store = OmnichannelConsentStore()
        assert store.get_consent("+15559999999") is None

    def test_valid_for_transactional(self):
        store = OmnichannelConsentStore()
        store.record_consent("+15551234567", ConsentType.PRIOR_EXPRESS)
        record = store.get_consent("+15551234567")
        assert record.is_valid_for_transactional()
        assert not record.is_valid_for_ai_marketing()

    def test_voice_consent_bridges_to_sms_campaign(self):
        store = OmnichannelConsentStore()
        store.record_consent("+15551234567", ConsentType.PRIOR_EXPRESS_WRITTEN, channel="voice", campaign_id="C1")
        record = store.get_consent("+15551234567", campaign_id="C1")
        assert record.channel == "voice"
        assert record.is_valid_for_ai_marketing()


class TestSMSPIIScrubber:
    def test_url_phi_param_scrubbed(self):
        scrubber = SMSPIIScrubber()
        result = scrubber.scrub_text("Visit https://portal.example.com/appt?patient_id=12345&dob=04/15/85")
        assert "patient_id=12345" not in result.scrubbed_text
        assert "[REDACTED]" in result.scrubbed_text
        assert result.scrubbed

    def test_mrn_url_param_scrubbed(self):
        scrubber = SMSPIIScrubber()
        result = scrubber.scrub_text("https://health.example.com/record?mrn=9876543")
        assert "mrn=9876543" not in result.scrubbed_text

    def test_ssn_in_text_still_scrubbed(self):
        scrubber = SMSPIIScrubber()
        result = scrubber.scrub_text("SSN: 123-45-6789 see portal at https://example.com?ssn=123-45-6789")
        assert "123-45-6789" not in result.scrubbed_text

    def test_clean_sms_not_modified(self):
        scrubber = SMSPIIScrubber()
        result = scrubber.scrub_text("Your appointment is confirmed for Monday at 2pm.")
        assert not result.scrubbed
        assert result.replacements_made == 0


class TestTwilioSMSAdapter:
    def test_opted_out_message_fails_compliance(self):
        store = OmnichannelConsentStore()
        store.record_opt_out("+15551234567")
        adapter = TwilioSMSAdapter(from_number="+18005550100", consent_store=store)
        msg = adapter.build_message(to="+15551234567", body="Your appointment is confirmed.")
        assert not msg.compliance_passed

    def test_phi_url_params_scrubbed_in_body(self):
        adapter = TwilioSMSAdapter(from_number="+18005550100", scrub_phi=True)
        msg = adapter.build_message(
            to="+15551234567",
            body="Details: https://portal.example.com/case?patient_id=99999",
        )
        assert "patient_id=99999" not in msg.body

    def test_inbound_stop_records_opt_out(self):
        store = OmnichannelConsentStore()
        adapter = TwilioSMSAdapter(from_number="+18005550100", consent_store=store)
        result = adapter.process_inbound(from_number="+15551234567", body="STOP")
        assert result.is_opt_out
        assert store.is_opted_out("+15551234567")


class TestPostCallSMSBuilder:
    def test_builds_case_summary(self):
        builder = PostCallSMSBuilder(portal_base_url="https://portal.example.com/cases")
        msg = builder.build(
            to_agent="+15559999999",
            from_number="+18005550100",
            caller_number="+15554445678",
            session_id="sess_abc123",
            intent="billing_inquiry",
            turn_count=5,
            escalation_reason="low_confidence",
        )
        assert "5678" in msg.body  # last 4 of caller
        assert "billing_inquiry" in msg.body.lower() or "Billing Inquiry" in msg.body
        assert "sess_abc123" in msg.body
        assert msg.compliance_passed

    def test_phi_stripped_from_summary(self):
        builder = PostCallSMSBuilder(scrub_phi=True)
        msg = builder.build(
            to_agent="+15559999999",
            from_number="+18005550100",
            caller_number="+15554445678",
            session_id="sess123",
            summary_text="Patient SSN 123-45-6789 called about billing.",
        )
        assert "123-45-6789" not in msg.body
