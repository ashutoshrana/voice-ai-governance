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
    from voice_ai_governance.pii import PIIScrubber
    manager = WarmTransferStateManager(pii_scrubber=PIIScrubber())
    session_id = manager.create_session()
    # Synthetic fixture attestation, not production contract evidence.
    manager.update_state(session_id, lambda state: state.platform_metadata.update(baa_verified=True))
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


@pytest.mark.parametrize("verified", [None, True, False, "true"])
def test_baa_attestation_propagated(verified):
    from types import SimpleNamespace
    sm = MagicMock()
    sm.get_state.return_value = SimpleNamespace(platform_metadata={"baa_verified": verified})
    sm.build_handoff_payload.return_value = SimpleNamespace(
        pii_scrubbed=True, collected_entities={}, consent_obtained=True,
        to_dict=dict,
    )
    payload = LiveKitWarmTransferAdapter(state_manager=sm).build_transfer_payload("s")
    assert payload["hipaa_audit"]["passed"] is (verified is True)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["transfer", "on_confidence_low"])
@pytest.mark.parametrize("evidence", [None, False, "true"])
async def test_baa_failure_blocks_publication_and_state_changes(publication, method, evidence):
    adapter, manager, session_id, ctx = publication
    manager.update_state(session_id, lambda state: state.platform_metadata.update(baa_verified=evidence))
    before = manager.get_state(session_id).to_dict()
    args = (ctx,) if method == "transfer" else (ctx, .2, .6)
    with pytest.raises(RuntimeError, match="HIPAA policy check"):
        await getattr(adapter, method)(*args, recipient_identity="reviewer")
    ctx.room.local_participant.publish_data.assert_not_awaited()
    ctx.room.disconnect.assert_not_awaited()
    assert manager.get_state(session_id).to_dict() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("audit", [None, False, "true", {}, {"passed": 1}, {"passed": "true"}])
async def test_absent_audit_blocks_publication(publication, monkeypatch, audit):
    adapter, _, _, ctx = publication
    monkeypatch.setattr(adapter, "build_transfer_payload", lambda *a, **kw: {"hipaa_audit": audit})
    with pytest.raises(RuntimeError, match="HIPAA policy check"):
        await adapter.transfer(ctx, recipient_identity="reviewer")
    ctx.room.local_participant.publish_data.assert_not_awaited()
    ctx.room.disconnect.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("strict", [False, True])
async def test_recording_policy_mode_controls_publication(publication, strict):
    from voice_ai_governance.compliance import HIPAAVoicePolicy
    adapter, manager, session_id, ctx = publication
    adapter._hipaa_policy = HIPAAVoicePolicy(block_on_violation=strict)
    manager.update_state(session_id, lambda state: state.platform_metadata.update(recording_active=True))
    manager.update_state(session_id, lambda state: setattr(state, "consent_obtained", False))
    if strict:
        with pytest.raises(RuntimeError, match="HIPAA policy check"):
            await adapter.transfer(ctx, recipient_identity="reviewer")
        ctx.room.local_participant.publish_data.assert_not_awaited()
    else:
        await adapter.transfer(ctx, recipient_identity="reviewer")
        ctx.room.local_participant.publish_data.assert_awaited_once()
    ctx.room.disconnect.assert_not_awaited()


@pytest.mark.parametrize("value", ["false", "true", 1, 0, [], {}, None, False])
def test_non_boolean_consent_is_not_consent(value):
    adapter = LiveKitWarmTransferAdapter()
    identity = adapter.extract_caller_identity(json.dumps({"consent_obtained": value}))
    assert identity["consent_obtained"] is False
    with pytest.raises(TCPAConsentError):
        adapter._assert_tcpa_consent({"consent_obtained": value})


@pytest.mark.parametrize("metadata", [None, 123, [], {}, "[]", "null"])
def test_malformed_metadata_has_safe_defaults(metadata):
    assert LiveKitWarmTransferAdapter().extract_caller_identity(metadata)["consent_obtained"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("session_id", [123, ["session"], {"id": "session"}, "", " "])
async def test_invalid_session_id_rejected_before_lookup(publication, session_id):
    adapter, _, _, ctx = publication
    ctx.job.metadata = json.dumps({"consent_obtained": True, "session_id": session_id})
    with pytest.raises(ValueError, match="session ID"):
        await adapter.transfer(ctx, recipient_identity="reviewer")
    ctx.room.local_participant.publish_data.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["transfer", "on_confidence_low"])
async def test_oversize_packet_does_not_publish_or_mutate(publication, monkeypatch, method):
    adapter, manager, sid, ctx = publication
    payload = adapter.build_transfer_payload(sid)
    payload["caller_summary"] = "界" * 6000  # Under 15K characters, over 15KiB UTF-8.
    monkeypatch.setattr(adapter, "build_transfer_payload", lambda *a, **k: dict(payload))
    before = manager.get_state(sid).to_dict()
    args = (ctx,) if method == "transfer" else (ctx, .2, .6)
    with pytest.raises(ValueError, match="packet limit"):
        await getattr(adapter, method)(*args, recipient_identity="reviewer")
    ctx.room.local_participant.publish_data.assert_not_awaited()
    ctx.room.disconnect.assert_not_awaited()
    assert manager.get_state(sid).to_dict() == before


def test_packet_limit_counts_exact_encoded_bytes():
    from voice_ai_governance.adapters.livekit import _encode_transfer_payload
    overhead = len(_encode_transfer_payload({"text": ""}))
    assert len(_encode_transfer_payload({"text": "x" * (15360 - overhead)})) == 15360
    with pytest.raises(ValueError, match="packet limit"):
        _encode_transfer_payload({"text": "x" * (15361 - overhead)})


@pytest.mark.asyncio
async def test_confidence_preflights_prospective_growth(publication, monkeypatch):
    from voice_ai_governance.adapters.livekit import _encode_transfer_payload
    adapter, manager, sid, ctx = publication
    payload = adapter.build_transfer_payload(sid)
    payload["caller_summary"] += "x" * (15360 - len(_encode_transfer_payload(payload)))
    assert len(_encode_transfer_payload(payload)) == 15360
    monkeypatch.setattr(adapter, "build_transfer_payload", lambda *a, **k: dict(payload))
    before = manager.get_state(sid).to_dict()
    with pytest.raises(ValueError, match="packet limit"):
        await adapter.on_confidence_low(ctx, .123456, .6, recipient_identity="reviewer")
    assert manager.get_state(sid).to_dict() == before
    ctx.room.local_participant.publish_data.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("score", [float("nan"), float("inf"), -1, 2, True, "0.2"])
async def test_invalid_confidence_no_side_effects(publication, score):
    adapter, manager, sid, ctx = publication
    before = manager.get_state(sid).to_dict()
    with pytest.raises(ValueError, match="confidence_score"):
        await adapter.on_confidence_low(ctx, score, .6, recipient_identity="reviewer")
    assert manager.get_state(sid).to_dict() == before
    ctx.room.local_participant.publish_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_confidence_packet_contains_prospective_fields(publication):
    adapter, manager, sid, ctx = publication
    await adapter.on_confidence_low(ctx, .1234, .6, recipient_identity="reviewer")
    raw = ctx.room.local_participant.publish_data.call_args.args[0]
    payload = json.loads(raw)
    assert payload["confidence_at_transfer"] == .1234
    assert payload["escalation_trigger"] == "low_confidence"
    assert manager.get_state(sid).escalation_confidence_score == .1234


@pytest.mark.asyncio
async def test_disappearing_session_not_published(publication, monkeypatch):
    adapter, manager, _, ctx = publication
    monkeypatch.setattr(manager, "update_state", lambda *a: None)
    with pytest.raises(ValueError, match="disappeared"):
        await adapter.on_confidence_low(ctx, .2, .6, recipient_identity="reviewer")
    ctx.room.local_participant.publish_data.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["baa", "terminal", "recipient"])
async def test_confidence_revalidates_before_publication(publication, monkeypatch, change):
    from voice_ai_governance.state import TransferStatus
    adapter, manager, sid, ctx = publication
    original = manager.update_state
    def changed_update(session_id, updater):
        if change == "baa":
            original(session_id, lambda state: state.platform_metadata.update(baa_verified=False))
        elif change == "terminal":
            original(session_id, lambda state: setattr(state, "status", TransferStatus.COMPLETED))
        else:
            ctx.room.remote_participants.clear()
        return original(session_id, updater)
    monkeypatch.setattr(manager, "update_state", changed_update)
    with pytest.raises((ValueError, RuntimeError)):
        await adapter.on_confidence_low(ctx, .2, .6, recipient_identity="reviewer")
    ctx.room.local_participant.publish_data.assert_not_awaited()
    ctx.room.disconnect.assert_not_awaited()
    if change != "recipient":
        assert manager.get_state(sid).escalation_trigger is None


@pytest.mark.parametrize("session_id", [
    "12345678-1234-4234-9234-123456789012",
    "abcdefab-cdef-4abc-8def-abcdefabcdef",
])
def test_internal_uuid_correlation_survives_false_positive_redaction(monkeypatch, session_id):
    import uuid

    from voice_ai_governance import state as state_module
    from voice_ai_governance.pii import PIIScrubber
    manager = state_module.WarmTransferStateManager(pii_scrubber=PIIScrubber())
    monkeypatch.setattr(state_module.uuid, "uuid4", lambda: uuid.UUID(session_id))
    sid = manager.create_session(platform_metadata={"baa_verified": True})
    manager.update_state(sid, lambda state: setattr(state, "primary_intent", "123-45-6789"))
    payload = LiveKitWarmTransferAdapter(state_manager=manager).build_transfer_payload(sid)
    assert payload["session_id"] == session_id
    assert "123-45-6789" not in payload["primary_intent"]
    assert "123-45-6789" not in payload["caller_summary"]


def test_sensitive_non_uuid_id_is_not_restored(monkeypatch):
    from voice_ai_governance import state as state_module
    from voice_ai_governance.pii import PIIScrubber
    manager = state_module.WarmTransferStateManager(pii_scrubber=PIIScrubber())
    monkeypatch.setattr(state_module.uuid, "uuid4", lambda: "123-45-6789")
    sid = manager.create_session(platform_metadata={"baa_verified": True})
    with pytest.raises(ValueError, match="noncanonical session identifier"):
        LiveKitWarmTransferAdapter(state_manager=manager).build_transfer_payload(sid)
