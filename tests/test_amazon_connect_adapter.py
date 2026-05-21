"""Tests for Amazon Connect voice-ai-governance adapter."""

from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest

from voice_ai_governance.adapters.amazon_connect import AmazonConnectAdapter, TCPAConsentError


class TestAmazonConnectAdapterInit:
    def test_default_region(self):
        adapter = AmazonConnectAdapter(instance_id="inst_001")
        assert adapter.instance_id == "inst_001"

    def test_custom_region(self):
        adapter = AmazonConnectAdapter(instance_id="inst_001", region_name="us-west-2")
        assert adapter.region_name == "us-west-2"

    def test_state_manager_injected(self):
        sm = MagicMock()
        adapter = AmazonConnectAdapter(instance_id="i", state_manager=sm)
        assert adapter._state_manager is sm


class TestTCPAConsentError:
    def test_is_runtime_error(self):
        exc = TCPAConsentError("no consent")
        assert isinstance(exc, RuntimeError)

    def test_message(self):
        exc = TCPAConsentError("TCPA violation")
        assert "TCPA" in str(exc) or "violation" in str(exc)


class TestBuildTransferPayload:
    def setup_method(self):
        self.adapter = AmazonConnectAdapter(instance_id="inst_001")
        sm = MagicMock()

        state_mock = types.SimpleNamespace(
            consent_obtained=True,
            consent_timestamp=1714500000.0,
            entities={},
            platform_metadata={},
        )
        sm.get_state.return_value = state_mock

        payload_mock = MagicMock()
        payload_mock.caller_summary = "Test call"
        payload_mock.primary_intent = "billing"
        payload_mock.sentiment = "neutral"
        payload_mock.consent_obtained = True
        payload_mock.compliance_flags = []
        payload_mock.pii_scrubbed = True
        payload_mock.transfer_reason = "confidence_escalation"
        payload_mock.escalation_trigger = "low_confidence"
        payload_mock.confidence_at_transfer = 0.42
        payload_mock.call_duration_seconds = 120.0
        payload_mock.turn_count = 5
        payload_mock.transfer_timestamp = "2026-05-01T00:00:00Z"
        sm.build_handoff_payload.return_value = payload_mock

        self.adapter._state_manager = sm

    def test_returns_warm_transfer_type(self):
        payload = self.adapter.build_transfer_payload("sess_001", "contact_001")
        assert payload.get("transferType") == "WARM"

    def test_attributes_present(self):
        payload = self.adapter.build_transfer_payload("sess_001", "contact_001")
        assert "attributes" in payload

    def test_tcpa_consent_required(self):
        sm = MagicMock()
        state_no_consent = types.SimpleNamespace(
            consent_obtained=False,
            consent_timestamp=None,
            entities={},
            platform_metadata={},
        )
        sm.get_state.return_value = state_no_consent
        self.adapter._state_manager = sm
        with pytest.raises((TCPAConsentError, Exception)):
            self.adapter.build_transfer_payload("sess_no_consent", "contact_001")
