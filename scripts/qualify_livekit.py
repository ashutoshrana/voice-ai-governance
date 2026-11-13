"""Exercise the adapter through installed LiveKit Python SDK serialization.

Run separately from pytest (tests/conftest.py stubs optional SDKs):
  python scripts/qualify_livekit.py

Requires the project's livekit extra. Loads the actual native FFI library, then
replaces only its request/event boundary with deterministic fixtures. No room,
credentials, network connection, or real packet delivery is involved. This is SDK
API/serialization qualification, not a live-call integration test.
"""
from __future__ import annotations

import asyncio
import ctypes
import inspect
import json
import sys
import uuid
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from livekit import rtc
from livekit.agents import JobContext  # noqa: F401
from livekit.rtc import participant
from livekit.rtc._ffi_client import FfiClient

from voice_ai_governance.adapters import livekit as adapter


class TransportBoundary:
    """Stub only transport: real SDK creates and populates request protobufs."""

    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.queue = self
        self.subscriptions = 0
        self.requests = 0
        self.raw = b""
        self.request_message = None

    def subscribe(self):
        self.subscriptions += 1
        return self

    def unsubscribe(self, queue):
        assert queue is self
        self.subscriptions -= 1

    def request(self, request):
        self.requests += 1
        # Copy pointer-backed bytes while SDK's ctypes allocation is still alive.
        message = request.publish_data
        self.raw = ctypes.string_at(message.data_ptr, message.data_len)
        self.request_message = participant.proto_ffi.FfiRequest()
        self.request_message.CopyFrom(request)
        response = participant.proto_ffi.FfiResponse()
        response.publish_data.async_id = 41
        return response

    async def wait_for(self, predicate):
        if self.outcome == "cancel":
            raise asyncio.CancelledError()
        event = participant.proto_ffi.FfiEvent()
        event.publish_data.async_id = 41
        if self.outcome == "error":
            event.publish_data.error = "synthetic transport failure"
        assert predicate(event)
        return event


async def qualify() -> dict:
    assert adapter._LIVEKIT_AVAILABLE, "Actual SDK did not import"
    assert inspect.iscoroutinefunction(rtc.LocalParticipant.publish_data)
    # This validates availability of the wheel's native library without any call.
    native_client = FfiClient.instance
    assert isinstance(native_client, FfiClient)
    outcomes = []
    for outcome in ("success", "error", "cancel"):
        transport = TransportBoundary(outcome)
        # Actual LocalParticipant method; bypass construction of a connected handle.
        local = object.__new__(rtc.LocalParticipant)
        local._ffi_handle = SimpleNamespace(handle=73)
        manager = adapter.WarmTransferStateManager(pii_scrubber=adapter.PIIScrubber())
        # Intentionally exercise the digit-heavy UUID card-regex false positive.
        # The adapter preserves this registered canonical correlation identifier.
        # Sensitive payload redaction remains enabled.
        with patch("voice_ai_governance.state.uuid.uuid4",
                   return_value=uuid.UUID("12345678-1234-4234-9234-123456789012")):
            session_id = manager.create_session(platform_metadata={"baa_verified": True})
        governed = adapter.LiveKitWarmTransferAdapter(state_manager=manager)
        context = SimpleNamespace(
            room=SimpleNamespace(local_participant=local, remote_participants={"reviewer": object()}),
            job=SimpleNamespace(metadata=json.dumps({"session_id": session_id, "consent_obtained": True})),
        )
        with patch.object(FfiClient, "_instance", transport):
            try:
                await governed.transfer(context, recipient_identity="reviewer")
            except participant.PublishDataError as error:
                assert outcome == "error"
                assert "synthetic transport failure" in str(error)
            except asyncio.CancelledError:
                assert outcome == "cancel"
            else:
                assert outcome == "success"
        assert transport.requests == 1
        assert transport.subscriptions == 0, "SDK must unsubscribe on every outcome"
        message = transport.request_message.publish_data
        assert message.local_participant_handle == 73
        assert message.reliable is True
        assert list(message.destination_identities) == ["reviewer"]
        assert message.topic == "voice_governance_transfer"
        payload = json.loads(transport.raw.decode("utf-8"))
        assert payload["session_id"] == session_id
        assert payload["hipaa_audit"]["passed"] is True
        assert isinstance(payload["pii_scrubbed"], bool)
        assert manager.get_state(session_id).status == adapter.TransferStatus.ACTIVE
        outcomes.append(outcome)
    assert FfiClient.instance is native_client
    return {
        "livekit_agents": version("livekit-agents"),
        "livekit_rtc": version("livekit"),
        "python": sys.version.split()[0],
        "native_library_loaded": True,
        "outcomes_verified": outcomes,
        "boundary": "real Python SDK serialization; synthetic FFI request/event transport",
        "live_room_or_call_verified": False,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(qualify()), indent=2))
