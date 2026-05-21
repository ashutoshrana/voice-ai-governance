"""Tests for AsyncWarmTransferStateManager."""
from __future__ import annotations
import asyncio
import pytest
from voice_ai_governance.async_state import AsyncWarmTransferStateManager
from voice_ai_governance.state import TransferStatus


@pytest.mark.asyncio
async def test_create_and_retrieve_session():
    mgr = AsyncWarmTransferStateManager()
    sid = await mgr.create_session(call_sid="CA001")
    state = await mgr.get_state(sid)
    assert state is not None
    assert state.call_sid == "CA001"
    assert state.status == TransferStatus.ACTIVE


@pytest.mark.asyncio
async def test_update_state_adds_turn():
    mgr = AsyncWarmTransferStateManager()
    sid = await mgr.create_session()
    await mgr.update_state(sid, lambda s: s.add_turn("user", "I need help", intent="support", confidence=0.9))
    state = await mgr.get_state(sid)
    assert state.turn_count == 1
    assert state.primary_intent == "support"


@pytest.mark.asyncio
async def test_concurrent_updates_are_safe():
    mgr = AsyncWarmTransferStateManager()
    sid = await mgr.create_session()
    async def add_turn(i: int):
        await mgr.update_state(sid, lambda s: s.add_turn("user", f"turn {i}"))
    await asyncio.gather(*[add_turn(i) for i in range(10)])
    state = await mgr.get_state(sid)
    assert state.turn_count == 10


@pytest.mark.asyncio
async def test_build_handoff_payload():
    mgr = AsyncWarmTransferStateManager()
    sid = await mgr.create_session(call_sid="CA002")
    await mgr.update_state(sid, lambda s: s.add_turn(
        "user", "I want to enroll", intent="enrollment", confidence=0.85,
        entities_detected={"program": "MBA"}
    ))
    payload = await mgr.build_handoff_payload(sid, reason="low_confidence")
    assert payload is not None
    assert payload.session_id == sid
    assert payload.transfer_reason == "low_confidence"
    assert "program" in payload.collected_entities


@pytest.mark.asyncio
async def test_initiate_transfer_changes_status():
    mgr = AsyncWarmTransferStateManager()
    sid = await mgr.create_session()
    ok = await mgr.initiate_transfer(sid)
    assert ok is True
    state = await mgr.get_state(sid)
    assert state.status == TransferStatus.TRANSFERRED


@pytest.mark.asyncio
async def test_get_state_missing_session_returns_none():
    mgr = AsyncWarmTransferStateManager()
    result = await mgr.get_state("nonexistent-session-id")
    assert result is None


@pytest.mark.asyncio
async def test_initiate_transfer_missing_session_returns_false():
    mgr = AsyncWarmTransferStateManager()
    ok = await mgr.initiate_transfer("nonexistent-id")
    assert ok is False


@pytest.mark.asyncio
async def test_close_session_marks_completed():
    mgr = AsyncWarmTransferStateManager()
    sid = await mgr.create_session()
    await mgr.close_session(sid)
    state = await mgr.get_state(sid)
    assert state.status == TransferStatus.COMPLETED
