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
