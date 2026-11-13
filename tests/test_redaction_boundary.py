"""Requested handoff redaction must fail closed at the shared boundary."""
import pytest
from voice_ai_governance.state import WarmTransferStateManager
from voice_ai_governance.async_state import AsyncWarmTransferStateManager


def test_sync_requires_scrubber_and_missing_session_stays_none():
    manager = WarmTransferStateManager()
    assert manager.build_handoff_payload("missing", "handoff") is None
    sid = manager.create_session()
    with pytest.raises(ValueError, match="scrubber"):
        manager.build_handoff_payload(sid, "handoff")
    assert manager.build_handoff_payload(sid, "handoff", scrub_pii=False) is not None


@pytest.mark.asyncio
async def test_async_requires_scrubber_and_missing_session_stays_none():
    manager = AsyncWarmTransferStateManager()
    assert await manager.build_handoff_payload("missing", "handoff") is None
    sid = await manager.create_session()
    with pytest.raises(ValueError, match="scrubber"):
        await manager.build_handoff_payload(sid, "handoff")
    assert await manager.build_handoff_payload(sid, "handoff", scrub_pii=False) is not None


def test_custom_pattern_correlation_exception_is_top_level_only(monkeypatch):
    import re

    from voice_ai_governance import state as state_module
    from voice_ai_governance.pii import PIIScrubber

    sid = "abcdefab-cdef-4abc-8def-abcdefabcdef"
    monkeypatch.setattr(state_module.uuid, "uuid4", lambda: sid)
    scrubber = PIIScrubber(custom_patterns={"internal": re.compile(re.escape(sid))})
    manager = WarmTransferStateManager(pii_scrubber=scrubber)
    manager.create_session()
    manager.update_state(sid, lambda state: setattr(state, "primary_intent", sid))
    payload = manager.build_handoff_payload(sid, "handoff")
    assert payload.session_id == sid
    assert payload.primary_intent == "[INTERNAL_REDACTED]"
    assert sid not in payload.caller_summary
