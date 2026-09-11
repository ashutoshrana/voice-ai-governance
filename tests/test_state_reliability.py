import asyncio
import json
import shutil
import subprocess
import time
import tempfile
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
import pytest
from voice_ai_governance.state import WarmTransferStateManager, TransferStatus
from voice_ai_governance.async_state import AsyncWarmTransferStateManager
from voice_ai_governance.pii import PIIScrubber

@pytest.fixture
def redis_socket(tmp_path):
    if not shutil.which('redis-server'):
        pytest.skip('redis-server needed for real Redis integration')
    pytest.importorskip('redis')
    directory = tempfile.TemporaryDirectory(prefix='vag-', dir='/private/tmp')
    path = directory.name + '/redis.sock'
    server = subprocess.Popen(['redis-server', '--port', '0', '--unixsocket', path, '--save', '', '--appendonly', 'no'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        import redis
        client = redis.Redis(unix_socket_path=path, socket_connect_timeout=.1, socket_timeout=.5, retry=redis.retry.Retry(redis.backoff.NoBackoff(), 0))
        for _ in range(100):
            try:
                if client.ping():
                    break
            except redis.ConnectionError:
                time.sleep(.02)
        assert client.ping()
        client.close()
        yield path
    finally:
        server.terminate()
        server.wait(timeout=5)
        directory.cleanup()

@pytest.mark.asyncio
async def test_async_cross_manager_transactions_and_terminal_retry(redis_socket):
    import redis.asyncio as redis
    client = redis.Redis(unix_socket_path=redis_socket)
    a, b = [AsyncWarmTransferStateManager(client) for _ in range(2)]
    try:
        sid = await a.create_session()
        await asyncio.gather(*[(a if i % 2 else b).update_state(sid, lambda s: s.add_turn('user', 'hello')) for i in range(30)])
        assert (await a.get_state(sid)).turn_count == 30
        assert await a.initiate_transfer(sid)
        assert await b.initiate_transfer(sid)
        await asyncio.gather(a.close_session(sid), b.initiate_transfer(sid))
        assert (await a.get_state(sid)).status == TransferStatus.COMPLETED
        assert not await b.initiate_transfer(sid)
    finally:
        await client.aclose()

def test_sync_cross_manager_transactions(redis_socket):
    import redis
    client = redis.Redis(unix_socket_path=redis_socket)
    managers = [WarmTransferStateManager(client) for _ in range(2)]
    try:
        sid = managers[0].create_session()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: managers[i % 2].update_state(sid, lambda s: s.add_turn('user', 'hello')), range(40)))
        assert managers[0].get_state(sid).turn_count == 40
    finally:
        client.close()

@pytest.mark.asyncio
async def test_complete_payload_redaction_and_failed_update_rollback():
    a = AsyncWarmTransferStateManager(pii_scrubber=PIIScrubber())
    sid = await a.create_session()
    def populate(s):
        s.add_turn('user', '', entities_detected={'name':'Secret Name', 'nested':[{'email':'secret@example.com'}]})
        s.primary_intent = 'secret@example.com'
        s.compliance_flags = ['secret@example.com']
    await a.update_state(sid, populate)
    payload = await a.build_handoff_payload(sid, 'secret@example.com')
    serialized = json.dumps(asdict(payload))
    assert 'Secret Name' not in serialized and 'secret@example.com' not in serialized
    assert payload.pii_scrubbed
    assert (await a.get_state(sid)).entities['name'].value == 'Secret Name'
    def failure(s):
        s.turn_count = 999
        raise ValueError('abort')
    with pytest.raises(ValueError):
        await a.update_state(sid, failure)
    assert (await a.get_state(sid)).turn_count == 1
    sync = WarmTransferStateManager(pii_scrubber=PIIScrubber())
    other = sync.create_session()
    sync.update_state(other, populate)
    assert 'Secret Name' not in json.dumps(asdict(sync.build_handoff_payload(other, 'secret@example.com')))

@pytest.mark.asyncio
async def test_redis_disconnect_propagates_without_local_fallback():
    from unittest.mock import AsyncMock
    from redis.exceptions import ConnectionError
    client = AsyncMock()
    client.get.side_effect = ConnectionError('disconnected')
    manager = AsyncWarmTransferStateManager(client)
    with pytest.raises(ConnectionError):
        await manager.build_handoff_payload('session', 'retry')
    assert manager._local_store == {}
