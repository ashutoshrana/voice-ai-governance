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

@pytest.fixture
def publication(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from voice_ai_governance.adapters import livekit
    from voice_ai_governance.state import WarmTransferStateManager

    monkeypatch.setattr(livekit, '_LIVEKIT_AVAILABLE', True)
    manager = WarmTransferStateManager()
    session_id = manager.create_session()
    room = SimpleNamespace(
        remote_participants={'reviewer': object(), 'bystander': object()},
        local_participant=SimpleNamespace(publish_data=AsyncMock()),
        disconnect=AsyncMock(),
    )
    ctx = SimpleNamespace(room=room, job=SimpleNamespace(metadata=json.dumps(
        {'session_id': session_id, 'consent_obtained': True})))
    return LiveKitWarmTransferAdapter(state_manager=manager), manager, session_id, ctx


@pytest.mark.asyncio
async def test_publication_targets_recipient_without_claiming_transfer(publication):
    from voice_ai_governance.state import TransferStatus
    adapter, manager, session_id, ctx = publication
    await adapter.transfer(ctx, recipient_identity='reviewer')
    assert ctx.room.local_participant.publish_data.call_args.kwargs['destination_identities'] == ['reviewer']
    ctx.room.disconnect.assert_not_awaited()
    assert manager.get_state(session_id).status == TransferStatus.ACTIVE


@pytest.mark.asyncio
@pytest.mark.parametrize('recipient', ['', 'missing', None])
async def test_invalid_recipient_has_no_effect(publication, recipient):
    adapter, manager, session_id, ctx = publication
    before = manager.get_state(session_id).to_dict()
    with pytest.raises(ValueError):
        await adapter.on_confidence_low(ctx, .2, .6, recipient_identity=recipient)
    ctx.room.local_participant.publish_data.assert_not_awaited()
    ctx.room.disconnect.assert_not_awaited()
    assert manager.get_state(session_id).to_dict() == before


@pytest.mark.asyncio
async def test_empty_room_never_broadcasts(publication):
    adapter, _, _, ctx = publication
    ctx.room.remote_participants.clear()
    with pytest.raises(ValueError):
        await adapter.transfer(ctx, recipient_identity='reviewer')
    ctx.room.local_participant.publish_data.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [RuntimeError('publish failed'), AttributeError('unsupported API')])
async def test_publication_failure_propagates_without_disconnect(publication, error):
    from voice_ai_governance.state import TransferStatus
    adapter, manager, session_id, ctx = publication
    ctx.room.local_participant.publish_data.side_effect = error
    with pytest.raises(type(error)):
        await adapter.transfer(ctx, recipient_identity='reviewer')
    ctx.room.disconnect.assert_not_awaited()
    assert manager.get_state(session_id).status == TransferStatus.ACTIVE


@pytest.mark.asyncio
async def test_cancelled_publication_does_not_disconnect(publication):
    import asyncio
    from voice_ai_governance.state import TransferStatus
    adapter, manager, session_id, ctx = publication
    ctx.room.local_participant.publish_data.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await adapter.transfer(ctx, recipient_identity='reviewer')
    ctx.room.disconnect.assert_not_awaited()
    assert manager.get_state(session_id).status == TransferStatus.ACTIVE


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['missing', 'completed', 'transferred', 'failed'])
async def test_invalid_session_not_published(publication, status):
    from voice_ai_governance.state import TransferStatus
    adapter, manager, session_id, ctx = publication
    if status == 'missing':
        ctx.job.metadata = json.dumps({'session_id': 'unknown', 'consent_obtained': True})
    else:
        manager.update_state(session_id, lambda state: setattr(state, 'status', TransferStatus(status)))
    with pytest.raises(ValueError):
        await adapter.transfer(ctx, recipient_identity='reviewer')
    ctx.room.local_participant.publish_data.assert_not_awaited()
    ctx.room.disconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_recipient_is_required(publication):
    adapter, _, _, ctx = publication
    with pytest.raises(TypeError):
        await adapter.transfer(ctx)
    ctx.room.local_participant.publish_data.assert_not_awaited()

@pytest.mark.asyncio
async def test_confidence_hook_publishes_without_transfer(publication):
    from voice_ai_governance.state import TransferStatus
    adapter, manager, session_id, ctx = publication
    await adapter.on_confidence_low(ctx, .2, .6, recipient_identity='reviewer')
    ctx.room.local_participant.publish_data.assert_awaited_once()
    ctx.room.disconnect.assert_not_awaited()
    assert manager.get_state(session_id).status == TransferStatus.ACTIVE


@pytest.mark.asyncio
async def test_missing_consent_blocks_publication(publication):
    adapter, manager, session_id, ctx = publication
    ctx.job.metadata = json.dumps({'session_id': session_id, 'consent_obtained': False})
    before = manager.get_state(session_id).to_dict()
    with pytest.raises(TCPAConsentError):
        await adapter.on_confidence_low(ctx, .2, .6, recipient_identity='reviewer')
    ctx.room.local_participant.publish_data.assert_not_awaited()
    assert manager.get_state(session_id).to_dict() == before


@pytest.mark.asyncio
async def test_context_required(publication):
    adapter, _, _, _ = publication
    with pytest.raises(ValueError):
        await adapter.transfer(None, recipient_identity='reviewer')
