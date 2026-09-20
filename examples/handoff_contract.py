"""Synthetic handoff conformance: real adapter, fake transport, no live SDK/calls."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

from voice_ai_governance.adapters.livekit import LiveKitWarmTransferAdapter
from voice_ai_governance.pii import PIIScrubber
from voice_ai_governance.state import TransferStatus, WarmTransferStateManager


@dataclass(frozen=True)
class Receipt:
    """Test fixture only; production events require authenticated provider provenance."""
    attempt_id: str
    session_id: str
    recipient: str
    outcome: str


class FakeRoom:
    def __init__(self, participants, failure=None):
        self.remote_participants = dict.fromkeys(participants)
        self.local_participant = self
        self.failure = failure
        self.packets = []
        self.disconnects = 0

    async def publish_data(self, data, **kwargs):
        if self.failure is not None:
            raise self.failure
        self.packets.append((json.loads(data), kwargs))

    async def disconnect(self):
        self.disconnects += 1


class ReceiptFixture:
    """In-memory assertion fixture, not a durable transfer engine or approval service."""
    def __init__(self, manager, session_id):
        self.manager = manager
        self.session_id = session_id
        self.attempt_id = 'synthetic-attempt'
        self.recipient = 'synthetic-reviewer'
        self.caller_present = True
        self.outcome = 'waiting'
        self.connection_receipts = 0
        self.completions = 0

    def receive(self, receipt):
        if (self.outcome != 'waiting' or not self.caller_present
                or receipt.attempt_id != self.attempt_id
                or receipt.session_id != self.session_id
                or receipt.recipient != self.recipient):
            return False
        if receipt.outcome == 'connected':
            self.connection_receipts += 1
            self.manager.initiate_transfer(self.session_id)
            self.completions += 1
            self.outcome = 'connected'
            return True
        if receipt.outcome in ('refused', 'unavailable'):
            self.outcome = receipt.outcome
        return False

    def hangup(self):
        self.caller_present = False
        self.outcome = 'caller_left'


SCENARIOS = (
    'published_only', 'connected', 'refused', 'unavailable', 'empty_participants',
    'publication_failure', 'cancellation', 'duplicate_receipt', 'caller_hangup',
    'late_receipt', 'wrong_attempt', 'wrong_recipient',
)


async def run_scenario(name):
    if name not in SCENARIOS:
        raise ValueError('Unknown fixture scenario')
    manager = WarmTransferStateManager(pii_scrubber=PIIScrubber())
    session_id = manager.create_session()
    fixture = ReceiptFixture(manager, session_id)
    failure = (RuntimeError('synthetic publication failure') if name == 'publication_failure'
               else asyncio.CancelledError() if name == 'cancellation' else None)
    room = FakeRoom([] if name == 'empty_participants' else [fixture.recipient, 'bystander'], failure)
    ctx = SimpleNamespace(room=room, job=SimpleNamespace(metadata=json.dumps(
        {'session_id': session_id, 'consent_obtained': True})))
    adapter = LiveKitWarmTransferAdapter(state_manager=manager)
    # Only bypass optional SDK import availability; adapter logic is unmodified.
    with patch('voice_ai_governance.adapters.livekit._LIVEKIT_AVAILABLE', True):
        try:
            await adapter.transfer(ctx, recipient_identity=fixture.recipient)
        except (ValueError, RuntimeError, asyncio.CancelledError):
            fixture.outcome = 'publication_stopped'
    status_after_publication = manager.get_state(session_id).status.value
    receipt = Receipt(fixture.attempt_id, session_id, fixture.recipient, 'connected')
    if name in ('empty_participants', 'publication_failure', 'cancellation'):
        fixture.receive(receipt)  # A late event cannot revive a stopped fixture attempt.
    elif name in ('connected', 'duplicate_receipt'):
        fixture.receive(receipt)
        if name == 'duplicate_receipt':
            fixture.receive(receipt)
    elif name in ('refused', 'unavailable', 'late_receipt'):
        fixture.receive(Receipt(fixture.attempt_id, session_id, fixture.recipient,
                                'refused' if name == 'refused' else 'unavailable'))
        if name == 'late_receipt':
            fixture.receive(receipt)
    elif name == 'caller_hangup':
        fixture.hangup()
        fixture.receive(receipt)
    elif name == 'wrong_attempt':
        fixture.receive(Receipt('old-attempt', session_id, fixture.recipient, 'connected'))
    elif name == 'wrong_recipient':
        fixture.receive(Receipt(fixture.attempt_id, session_id, 'bystander', 'connected'))
    return {
        'scenario': name,
        'packets_submitted': len(room.packets),
        'destinations': [p[1]['destination_identities'] for p in room.packets],
        'status_after_publication': status_after_publication,
        'final_status': manager.get_state(session_id).status.value,
        'adapter_disconnects': room.disconnects,
        'caller_present': fixture.caller_present,
        'outcome': fixture.outcome,
        'synthetic_connection_receipts': fixture.connection_receipts,
        'completion_effects': fixture.completions,
    }


def assert_contract(row):
    """Independent output invariants; a publish-as-completion negative control fails."""
    assert row['status_after_publication'] == TransferStatus.ACTIVE.value
    assert row['adapter_disconnects'] == 0
    assert all(d == ['synthetic-reviewer'] for d in row['destinations'])
    assert row['completion_effects'] == row['synthetic_connection_receipts'] <= 1
    assert (row['final_status'] == TransferStatus.TRANSFERRED.value) == (row['completion_effects'] == 1)
    if not row['caller_present']:
        assert row['completion_effects'] == 0


async def main():
    rows = [await run_scenario(name) for name in SCENARIOS]
    for row in rows:
        assert_contract(row)
    print(json.dumps({'evidence': 'synthetic fixtures; no live SDK or telephony verification',
                      'scenarios_checked': len(rows), 'results': rows}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
