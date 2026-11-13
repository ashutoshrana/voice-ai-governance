# LiveKit context publication

The adapter submits one scrubbed JSON packet to one explicitly selected room
participant. It does **not** perform SIP REFER, dial a human, wait for acceptance,
disconnect the caller, or mark a transfer complete. Use LiveKit's native
[transfer workflow](https://docs.livekit.io/telephony/features/transfers/warm/)
for those operations. A packet is not a SIP header or native transfer request.

## Register trusted state before dispatch

Install `voice-ai-governance[livekit]>=0.6.0`. This example uses one process; with separate
dispatch and worker processes, use the same Redis-backed state manager/storage.
Never create an unrelated session ID in the worker and expect dispatch metadata
to locate it. The application owns authentication and recipient authorization.

```python
import json
import time
from voice_ai_governance.state import WarmTransferStateManager
from voice_ai_governance.pii import PIIScrubber
from voice_ai_governance.adapters.livekit import LiveKitWarmTransferAdapter

manager = WarmTransferStateManager(pii_scrubber=PIIScrubber())
adapter = LiveKitWarmTransferAdapter(state_manager=manager)

def prepare_dispatch(*, consent_verified: bool, baa_verified: bool) -> str:
    # Supply these only after your trusted review process, not from caller input.
    if consent_verified is not True or baa_verified is not True:
        raise ValueError("Required deployment evidence has not been verified")
    session_id = manager.create_session(platform_metadata={"baa_verified": baa_verified})
    def record_consent(state):
        state.consent_obtained = consent_verified
        state.consent_timestamp = time.time()
    manager.update_state(session_id, record_consent)
    return json.dumps({"session_id": session_id, "consent_obtained": consent_verified})

# Set the returned JSON as the server-side dispatch request's job metadata.
# Do not hardcode True in production to bypass evidence collection.
# job_metadata = prepare_dispatch(consent_verified=..., baa_verified=...)

async def publish_handoff(ctx, authorized_recipient_identity: str):
    # ctx is the connected JobContext for that dispatch. The host application
    # supplies an authorized participant identity that is present in this room.
    await adapter.on_confidence_low(
        ctx, confidence_score=0.42, threshold=0.65,
        recipient_identity=authorized_recipient_identity,
    )
```

The adapter does not grant access merely because a participant is present.
Keep job metadata and session evidence under trusted application control. The
consent flag is a configured safeguard, not a determination of which consent law
applies. See [policy semantics](POLICY_SEMANTICS.md) for the BAA default, explicit
opt-outs after applicability review, and advisory versus strict policy behavior.

## Migration to 0.6.0 from 0.5.1

These checks intentionally reject inputs previously accepted: truthy non-boolean
consent, malformed session IDs, nonfinite/out-of-range confidence values, and
oversize packets. Update dispatch producers and handle `ValueError` /
`TCPAConsentError` explicitly; do not coerce consent strings to booleans.

## Failure and size contract

- The shared handoff builder preserves registered canonical UUIDv4 correlation IDs if pattern redaction
  falsely matches their digit groups. Other IDs changed by redaction are rejected,
  not silently restored or transmitted under a corrupted correlation key. Other
  payload fields remain subject to the configured scrubber. This top-level UUID
  correlation exception (shared by sync/async state managers and their adapters)
  also applies to custom redaction patterns: do not put
  sensitive data in that schema field or treat it as a hidden/anonymized ID.
- Consent must be literal JSON `true`, not `"true"`, `"false"`, `1`, or another
  truthy value. Session IDs must be nonempty strings referencing active or
  escalating registered state. Malformed metadata fails closed.
- The complete encoded packet must fit **15 KiB (15,360 UTF-8 bytes)**, including
  JSON and audit fields. This follows LiveKit's documented reliable-packet
  [user-payload limit](https://docs.livekit.io/transport/data/packets/).
  Oversize packets raise `ValueError` before publication, without truncation.
  Reduce application context or design a separately authenticated/authorized
  retrieval reference. This library does not create or secure such references.
- The confidence hook validates finite scores/thresholds in `[0, 1]` and prepares
  the exact prospective bytes before recording escalation. Oversize input causes
  no escalation mutation. It records intent, then publishes the same bytes; an
  SDK failure/cancellation can leave that intent recorded, not a completed transfer.
- Session/policy checks are repeated around recording. Redis state updates are
  transactional, but room membership, external evidence and network delivery are
  not in that transaction. Concurrent changes after validation remain a host concern.
- Failed/missing HIPAA policy decisions block publication. SDK exceptions and
  cancellation propagate. No failure path disconnects the room. Reliable packets
  remain best-effort, not a durable delivery/receipt guarantee.

## Installed-SDK qualification (2026-11-13)

`python scripts/qualify_livekit.py` runs separately from pytest, whose core test
configuration stubs optional SDK imports. On Python 3.14.8, it passed with:

| Agents | RTC | Boundary tested |
|---|---|---|
| 1.8.7 | 1.1.20 | Declared minimum Agents graph |
| 1.8.11 | 1.2.0 | Latest available 1.x graph on review date |

Both environments passed `pip check`. The script imports the installed SDK and
calls its actual `LocalParticipant.publish_data`, inspecting the real protobuf
request and encoded bytes. Only the FFI request/event transport is substituted.
It checks one-recipient targeting, reliability/topic, SDK error/cancellation
propagation and event-queue cleanup. Native library initialization is exercised.

This is **not live-room, SIP, audio, credential, provider or telephony acceptance
qualification**. It does not prove end-user delivery or every intervening SDK
release. The CI boundary job repeats minimum/latest 1.x installations without
credentials; the host must still test its deployed topology end to end.
