"""Tests for LiveKit voice-ai-governance adapter."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from voice_ai_governance.adapters.livekit import LiveKitWarmTransferAdapter, TCPAConsentError


class TestLiveKitAdapterInit:
    def test_default_init(self):
        adapter = LiveKitWarmTransferAdapter()
        assert adapter is not None

    def test_state_manager_injected(self):
        sm = MagicMock()
        adapter = LiveKitWarmTransferAdapter(state_manager=sm)
        assert adapter._state_manager is sm


class TestTCPAConsentError:
    def test_is_exception(self):
        exc = TCPAConsentError(session_id="s1", missing_field="consent_obtained")
        assert isinstance(exc, Exception)

    def test_session_id_attr(self):
        exc = TCPAConsentError(session_id="sess-42", missing_field="consent_obtained")
        assert exc.session_id == "sess-42"


class TestExtractCallerIdentity:
    def setup_method(self):
        self.adapter = LiveKitWarmTransferAdapter()

    def test_parses_valid_metadata(self):
        metadata = json.dumps(
            {"session_id": "s1", "consent_obtained": True, "ani": "+15551234567"}
        )
        identity = self.adapter.extract_caller_identity(metadata)
        assert identity["session_id"] == "s1"
        assert identity["consent_obtained"] is True

    def test_missing_metadata_defaults_safe(self):
        identity = self.adapter.extract_caller_identity(None)
        assert identity["consent_obtained"] is False

    def test_malformed_metadata_defaults_safe(self):
        identity = self.adapter.extract_caller_identity("not-json")
        assert identity["consent_obtained"] is False


class TestBuildTransferPayload:
    def setup_method(self):
        self.adapter = LiveKitWarmTransferAdapter()
        sm = MagicMock()
        # Return None so build_transfer_payload uses the fallback dict path
        sm.build_handoff_payload.return_value = None
        self.adapter._state_manager = sm

    def test_returns_dict(self):
        result = self.adapter.build_transfer_payload("sess_001")
        assert isinstance(result, dict)

    def test_session_id_in_result(self):
        result = self.adapter.build_transfer_payload("sess_001")
        assert result.get("session_id") == "sess_001"


class TestAssertTCPAConsent:
    def setup_method(self):
        self.adapter = LiveKitWarmTransferAdapter()

    def test_raises_when_consent_false(self):
        identity = {"session_id": "s1", "consent_obtained": False}
        with pytest.raises(TCPAConsentError):
            self.adapter._assert_tcpa_consent(identity)

    def test_raises_when_consent_missing(self):
        identity = {"session_id": "s1"}
        with pytest.raises(TCPAConsentError):
            self.adapter._assert_tcpa_consent(identity)

    def test_passes_when_consent_true(self):
        identity = {"session_id": "s1", "consent_obtained": True}
        self.adapter._assert_tcpa_consent(identity)
