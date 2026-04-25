"""Tests for PII detection and scrubbing."""

import pytest
from voice_ai_governance.pii import PIIPattern, PIIScrubber


class TestPIIScrubber:
    def test_ssn_scrubbed(self):
        scrubber = PIIScrubber()
        result = scrubber.scrub_text("My SSN is 123-45-6789.")
        assert "[SSN_REDACTED]" in result.scrubbed_text
        assert "123-45-6789" not in result.scrubbed_text
        assert PIIPattern.SSN in result.patterns_found

    def test_phone_scrubbed(self):
        scrubber = PIIScrubber()
        result = scrubber.scrub_text("Call me at 555-123-4567 tomorrow.")
        assert "[PHONE_REDACTED]" in result.scrubbed_text
        assert PIIPattern.PHONE in result.patterns_found

    def test_email_scrubbed(self):
        scrubber = PIIScrubber()
        result = scrubber.scrub_text("My email is user@example.com please.")
        assert "[EMAIL_REDACTED]" in result.scrubbed_text

    def test_credit_card_scrubbed(self):
        scrubber = PIIScrubber()
        result = scrubber.scrub_text("Card number 4111 1111 1111 1111.")
        assert "[CARD_REDACTED]" in result.scrubbed_text

    def test_clean_text_no_scrubbing(self):
        scrubber = PIIScrubber()
        text = "I have a billing question about my invoice."
        result = scrubber.scrub_text(text)
        assert not result.scrubbed
        assert result.replacements_made == 0
        assert result.scrubbed_text == text

    def test_selective_patterns(self):
        scrubber = PIIScrubber(patterns=[PIIPattern.SSN])
        result = scrubber.scrub_text("SSN 123-45-6789 email user@example.com")
        assert "[SSN_REDACTED]" in result.scrubbed_text
        assert "user@example.com" in result.scrubbed_text  # Email not scrubbed

    def test_dict_scrubbing_pii_keys(self):
        scrubber = PIIScrubber()
        data = {
            "topic": "billing",
            "ssn": "123-45-6789",
            "account_number": "9876543210",
        }
        scrubbed, was_scrubbed = scrubber.scrub_dict(data)
        assert was_scrubbed
        assert "[SSN_REDACTED]" in scrubbed["ssn"]
        assert "[ACCOUNT_NUMBER_REDACTED]" in scrubbed["account_number"]
        assert scrubbed["topic"] == "billing"

    def test_dict_scrubbing_clean_data(self):
        scrubber = PIIScrubber()
        data = {"topic": "billing inquiry", "intent": "billing"}
        scrubbed, was_scrubbed = scrubber.scrub_dict(data)
        assert not was_scrubbed
        assert scrubbed == data

    def test_multiple_pii_in_transcript(self):
        scrubber = PIIScrubber()
        text = "Hi, my SSN is 123-45-6789 and my email is test@test.com"
        result = scrubber.scrub_text(text)
        assert result.replacements_made >= 2
        assert "123-45-6789" not in result.scrubbed_text
        assert "test@test.com" not in result.scrubbed_text

    def test_original_length_tracked(self):
        scrubber = PIIScrubber()
        text = "My SSN is 123-45-6789"
        result = scrubber.scrub_text(text)
        assert result.original_length == len(text)
