import json
import pytest
from voice_ai_governance.state import WarmTransferStateManager, TransferStatus
from voice_ai_governance.async_state import AsyncWarmTransferStateManager
from voice_ai_governance.pii import PIIScrubber


def test_returned_state_cannot_mutate_stored_state():
    manager = WarmTransferStateManager()
    metadata = {'nested': {'flag': False}}
    sid = manager.create_session(platform_metadata=metadata)
    metadata['nested']['flag'] = True
    state = manager.get_state(sid)
    assert state.platform_metadata['nested']['flag'] is False
    state.status = TransferStatus.COMPLETED
    assert manager.get_state(sid).status == TransferStatus.ACTIVE
    state = manager.update_state(sid, lambda s: s.add_turn('user', 'hello'))
    state.turn_count = 999
    assert manager.get_state(sid).turn_count == 1


@pytest.mark.asyncio
async def test_async_snapshot_cannot_reopen_closed_session():
    manager = AsyncWarmTransferStateManager()
    sid = await manager.create_session()
    await manager.close_session(sid)
    state = await manager.get_state(sid)
    state.status = TransferStatus.ACTIVE
    assert not await manager.initiate_transfer(sid)
    saved = await manager.update_state(sid, lambda s: s.add_turn('user', 'hello'))
    saved.turn_count = 999
    assert (await manager.get_state(sid)).turn_count == 1


def test_pii_in_keys_removed_and_collisions_fail_closed():
    scrubber = PIIScrubber()
    result, changed = scrubber.scrub_dict({'nested': [{'caller@example.com': 'safe'}]})
    assert changed and 'caller@example.com' not in json.dumps(result)
    with pytest.raises(ValueError, match='collision'):
        scrubber.scrub_dict({'a@example.com': 1, 'b@example.com': 2})
