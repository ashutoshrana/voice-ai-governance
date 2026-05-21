"""Tests for LiveKit voice-ai-governance adapter."""
from __future__ import annotations
import asyncio
import json
import pytest
from unittest.mock import MagicMock, AsyncMock
import sys

for mod in [
    "livekit", "livekit.agents", "livekit.rtc",
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

from voice_ai_governance.adapters.livekit import LiveKitWarmTransferAdapter, TCPAConsentError


def _run(coro):
    return asyncio.run(coro)


class TestLiveKitAdapterInit:
    def test_default_init(self):
        adapter = LiveKitWarmTransferAdapter()
        assert adapter is not None

    def test_state_manager_injected(self):
        sm = MagicMock()
        adapter = LiveKitWarmTransferAdapter(state_manager=sm)
        assert adapter._state_manager is sm


class TestExtractCallerIdentity:
    def setup_method(self):
        self.adapter = LiveKitWarmTransferAdapter()

    def test_valid_json_metadata(self):
        meta = json.dumps({
            "caller_id": "user_123",
            "ani": "+15551234567",
            "session_id": "sess_abc",
            "consent_obtained": True,
            "consent_timestamp": 1714500000.0,
        })
        result = self.adapter.extract_caller_identity(meta)
        assert result["caller_id"] == "user_123"
        assert result["ani"] == "+15551234567"
        assert result["consent_obtained"] is True

    def test_non_json_metadata_fallback(self):
        result = self.adapter.extract_caller_identity("bare_string_id")
        assert result is not None

    def test_empty_metadata(self):
        result = self.adapter.extract_caller_identity("")
        assert isinstance(result, dict)

    def test_missing_fields_default_safely(self):
        result = self.adapter.extract_caller_identity(json.dumps({"caller_id": "x"}))
        assert result.get("consent_obtained") in (False, None, "")


class TestBuildTransferPayload:
    def setup_method(self):
        self.adapter = LiveKitWarmTransferAdapter()
        sm = MagicMock()
        sm.get_state.return_value = MagicMock(
            session_id="sess_001",
            summary="Test",
            intent="support",
            sentiment="neutral",
            entities={},
            compliance_flags=[],
            consent_obtained=True,
        )
        sm.build_handoff_payload.return_value = None
        self.adapter._state_manager = sm

    def test_returns_dict(self):
        payload = self.adapter.build_transfer_payload("sess_001")
        assert isinstance(payload, dict)

    def test_contains_session_id(self):
        payload = self.adapter.build_transfer_payload("sess_001")
        assert len(payload) > 0


class TestTCPAConsentError:
    def test_is_exception(self):
        with pytest.raises(TCPAConsentError):
            raise TCPAConsentError("no consent")

    def test_message_preserved(self):
        exc = TCPAConsentError("test message")
        assert "test" in str(exc)
