import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from examples.handoff_contract import SCENARIOS, assert_contract, run_scenario


@pytest.mark.parametrize('name', SCENARIOS)
def test_handoff_contract(name):
    row = asyncio.run(run_scenario(name))
    assert_contract(row)
    if name in ('connected', 'duplicate_receipt'):
        assert row['completion_effects'] == 1
    else:
        assert row['completion_effects'] == 0
    if name in ('empty_participants', 'publication_failure', 'cancellation'):
        assert row['packets_submitted'] == 0
        assert row['outcome'] == 'publication_stopped'
    if name != 'caller_hangup':
        assert row['caller_present']


def test_negative_control_packet_is_not_connection(monkeypatch):
    from voice_ai_governance.adapters.livekit import LiveKitWarmTransferAdapter
    original = LiveKitWarmTransferAdapter.transfer

    async def unsafe_transfer(self, ctx, *args, **kwargs):
        await original(self, ctx, *args, **kwargs)
        # Deliberately reproduce the old bug: treat packet submission as transfer.
        session_id = json.loads(ctx.job.metadata)['session_id']
        self._state_manager.initiate_transfer(session_id)

    monkeypatch.setattr(LiveKitWarmTransferAdapter, 'transfer', unsafe_transfer)
    row = asyncio.run(run_scenario('published_only'))
    assert row['packets_submitted'] == 1
    assert row['synthetic_connection_receipts'] == 0
    with pytest.raises(AssertionError):
        assert_contract(row)


def test_cli_json_no_credentials():
    result = subprocess.run([sys.executable, 'examples/handoff_contract.py'],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, check=True)
    report = json.loads(result.stdout)
    assert report['scenarios_checked'] == len(SCENARIOS)
    assert 'synthetic' in report['evidence']
    for row in report['results']:
        assert_contract(row)
