"""
LiveKit Agents v1.6+ warm transfer adapter for voice-ai-governance.

Integrates TCPA consent gating, HIPAA compliance enforcement, PII scrubbing,
and structured warm transfer handoff into LiveKit Agents VoicePipelineAgent
deployments.

Regulatory citations:
  - TCPA: 47 U.S.C. § 227 (consent required before autodialed/prerecorded calls)
  - HIPAA: 45 CFR § 164 (PHI minimum necessary, transfer payload scrubbing)
  - EU AI Act: Article 14 (human oversight capability for high-risk AI systems)

Install:
    pip install "livekit-agents>=1.6.0" "voice-ai-governance[livekit]"

Usage:
    from livekit.agents import JobContext, WorkerOptions, cli
    from voice_ai_governance.adapters.livekit import LiveKitWarmTransferAdapter

    adapter = LiveKitWarmTransferAdapter()

    async def entrypoint(ctx: JobContext):
        await ctx.connect()
        # On confidence gate trigger:
        await adapter.on_confidence_low(ctx, confidence_score=0.42, threshold=0.65,
                                        recipient_identity="authorized-recipient")

    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

try:
    from livekit.agents import JobContext
    from livekit.rtc import DataPacket, DataPacketKind
    _LIVEKIT_AVAILABLE = True
except ImportError:
    JobContext = None  # type: ignore[assignment,misc]
    DataPacket = None  # type: ignore[assignment]
    DataPacketKind = None  # type: ignore[assignment]
    _LIVEKIT_AVAILABLE = False

from voice_ai_governance.compliance import HIPAAVoicePolicy
from voice_ai_governance.pii import PIIScrubber
from voice_ai_governance.state import TransferStatus, WarmTransferStateManager

__all__ = [
    "LiveKitWarmTransferAdapter",
    "TCPAConsentError",
]

logger = logging.getLogger(__name__)

_TCPA_CITATION = "47 U.S.C. § 227"
_HIPAA_CITATION = "45 CFR § 164"
_EU_AI_ACT_CITATION = "EU AI Act Article 14 (human oversight capability)"

# Minimum consent fields that must be present and truthy in job metadata
_REQUIRED_CONSENT_FIELDS = ("consent_obtained",)

# LiveKit data channel used for SIP transfer signaling
_TRANSFER_CHANNEL = "voice_governance_transfer"


class TCPAConsentError(Exception):
    """Raised when required consent metadata is absent.

    The application decides how to handle the call and validates the underlying
    consent record. This check alone does not establish legal compliance.
    """

    def __init__(
        self,
        session_id: str = "",
        missing_field: str = "consent_obtained",
        citation: str = _TCPA_CITATION,
    ) -> None:
        self.session_id = session_id
        self.missing_field = missing_field
        self.citation = citation
        super().__init__(
            f"TCPA consent not documented (session={session_id!r}, "
            f"missing_field={missing_field!r}). "
            f"Prior express written consent required under {citation}."
        )


class LiveKitWarmTransferAdapter:
    """Build and publish scrubbed handoff context to one selected room participant.

    The application authorizes the recipient and owns native call orchestration.
    Publication is not proof of receipt, human acceptance, or legal compliance.

    Args:
        state_manager: WarmTransferStateManager instance. If None, a stateless
            in-process instance is created (suitable for single-server deployments).
        hipaa_policy: HIPAAVoicePolicy instance. Defaults to policy with PHI
            scrubbing enabled on transfer.

    Example:
        adapter = LiveKitWarmTransferAdapter(
            state_manager=WarmTransferStateManager(redis_client=redis_conn),
        )

        async def entrypoint(ctx: JobContext):
            await ctx.connect()
            session_id = adapter.extract_caller_identity(ctx.job.metadata)["session_id"]
            # Pipeline runs; confidence gate fires at some threshold:
            await adapter.on_confidence_low(
                ctx, confidence_score=0.41, threshold=0.65,
                recipient_identity=authorized_recipient_identity,
            )
    """

    def __init__(
        self,
        state_manager: Optional[WarmTransferStateManager] = None,
        hipaa_policy: Optional[HIPAAVoicePolicy] = None,
    ) -> None:
        self._state_manager = state_manager or WarmTransferStateManager(
            pii_scrubber=PIIScrubber()
        )
        self._hipaa_policy = hipaa_policy or HIPAAVoicePolicy(
            scrub_phi_before_transfer=True
        )

    # ------------------------------------------------------------------
    # Identity extraction
    # ------------------------------------------------------------------

    def extract_caller_identity(self, metadata_str: Optional[str]) -> Dict[str, Any]:
        """
        Parse LiveKit job metadata into a normalised caller identity dict.

        ``ctx.job.metadata`` is an opaque string set by the server-side dispatch
        call. Callers SHOULD embed a JSON object with at least ``session_id`` and
        ``consent_obtained``; this method degrades gracefully when fields are absent
        rather than crashing the agent entrypoint.

        Returns:
            Dict with keys: caller_id, ani, session_id, consent_obtained,
            consent_timestamp. All fields default to safe falsy values when missing.
        """
        defaults: Dict[str, Any] = {
            "caller_id": None,
            "ani": None,
            "session_id": None,
            "consent_obtained": False,
            "consent_timestamp": None,
        }

        if not metadata_str:
            return defaults

        try:
            parsed = json.loads(metadata_str)
            if not isinstance(parsed, dict):
                logger.warning(
                    "LiveKit job metadata is valid JSON but not a dict; "
                    "falling back to defaults"
                )
                return defaults
        except (json.JSONDecodeError, ValueError) as exc:
            # Metadata may legitimately be a plain string identifier in non-PSTN
            # deployments; treat the whole string as caller_id rather than failing.
            logger.debug("job.metadata is not JSON (%s); treating as raw caller_id", exc)
            return {**defaults, "caller_id": metadata_str}

        return {
            "caller_id": parsed.get("caller_id") or parsed.get("callerId"),
            "ani": parsed.get("ani") or parsed.get("from"),
            "session_id": parsed.get("session_id") or parsed.get("sessionId"),
            "consent_obtained": bool(parsed.get("consent_obtained", False)),
            "consent_timestamp": parsed.get("consent_timestamp")
            or parsed.get("consentTimestamp"),
        }

    # ------------------------------------------------------------------
    # TCPA consent gate
    # ------------------------------------------------------------------

    def _assert_tcpa_consent(self, identity: Dict[str, Any]) -> None:
        """
        Raise TCPAConsentError if prior express consent is not documented.

        Called at the start of ``transfer`` and ``on_confidence_low`` so that
        no transfer-related processing occurs without a consent record. The gate
        is intentionally strict: a missing field is treated identically to an
        explicit False — absence of documented consent is not consent.
        """
        for field_name in _REQUIRED_CONSENT_FIELDS:
            if not identity.get(field_name):
                raise TCPAConsentError(
                    session_id=str(identity.get("session_id", "")),
                    missing_field=field_name,
                )

    # ------------------------------------------------------------------
    # Payload construction
    # ------------------------------------------------------------------

    def build_transfer_payload(
        self,
        session_id: str,
        reason: str = "confidence_escalation",
        scrub_pii: bool = True,
    ) -> Dict[str, Any]:
        """
        Build a LiveKit SIP-compatible warm transfer payload dict.

        Retrieves accumulated conversation state from WarmTransferStateManager,
        runs HIPAA compliance check on the payload context, and serialises the
        result to a dict that can be published over the LiveKit data channel or
        passed to a SIP REFER/Replaces header via the LiveKit SIP trunk SDK.

        Args:
            session_id: Conversation session identifier previously registered
                with the state manager.
            reason: Transfer reason code forwarded to the contact center.
            scrub_pii: When True (default), PIIScrubber runs over entity values
                before the payload leaves the adapter. Required for HIPAA 45 CFR
                § 164.514 minimum necessary standard.

        Returns:
            Dict with ``_meta``, ``session_id``, ``reason``, ``hipaa_audit``,
            and all HandoffPayload fields.  Returns a minimal skeleton dict when
            no session state is available so callers always get a serialisable
            object.
        """
        handoff = self._state_manager.build_handoff_payload(
            session_id=session_id,
            reason=reason,
            scrub_pii=scrub_pii,
        )

        if handoff is None:
            return {
                "_meta": {
                    "adapter": "livekit",
                    "transfer_channel": _TRANSFER_CHANNEL,
                    "regulatory_citations": {
                        "tcpa": _TCPA_CITATION,
                        "hipaa": _HIPAA_CITATION,
                        "eu_ai_act": _EU_AI_ACT_CITATION,
                    },
                    "generated_at": time.time(),
                },
                "session_id": session_id,
                "reason": reason,
                "state_available": False,
            }

        hipaa_context = {
            "transfer_initiated": True,
            "phi_scrubbed": handoff.pii_scrubbed,
            "entities": handoff.collected_entities,
            "consent_obtained": handoff.consent_obtained,
        }
        hipaa_result = self._hipaa_policy.check(hipaa_context)
        for violation in hipaa_result.violations:
            self._hipaa_policy.on_violation(violation)

        payload = handoff.to_dict()
        payload["_meta"] = {
            "adapter": "livekit",
            "transfer_channel": _TRANSFER_CHANNEL,
            "regulatory_citations": {
                "tcpa": _TCPA_CITATION,
                "hipaa": _HIPAA_CITATION,
                "eu_ai_act": _EU_AI_ACT_CITATION,
            },
            "generated_at": time.time(),
        }
        payload["hipaa_audit"] = {
            "passed": hipaa_result.passed,
            "violations_count": len(hipaa_result.violations),
            "required_actions": hipaa_result.required_actions,
            "audit_log": hipaa_result.audit_log,
        }
        return payload

    # ------------------------------------------------------------------
    # Core transfer execution
    # ------------------------------------------------------------------

    def _publication_session(self, ctx: Any, recipient_identity: str) -> str:
        """Validate the explicit destination and current local session before use."""
        if not _LIVEKIT_AVAILABLE:
            raise RuntimeError("livekit-agents is not installed; install voice-ai-governance[livekit]")
        if ctx is None:
            raise ValueError("A connected LiveKit context is required")
        if not isinstance(recipient_identity, str) or not recipient_identity.strip():
            raise ValueError("An explicit recipient identity is required")
        participants = getattr(getattr(ctx, "room", None), "remote_participants", {})
        if recipient_identity not in participants:
            raise ValueError("The selected recipient is not present in the room")
        metadata = getattr(getattr(ctx, "job", None), "metadata", None)
        identity = self.extract_caller_identity(metadata)
        self._assert_tcpa_consent(identity)
        session_id = identity.get("session_id")
        state = self._state_manager.get_state(session_id) if session_id else None
        if state is None or state.status not in (TransferStatus.ACTIVE, TransferStatus.ESCALATING):
            raise ValueError("An active or escalating registered session is required")
        return session_id

    async def transfer(
        self,
        ctx: Any,
        reason: str = "confidence_escalation",
        *,
        recipient_identity: str,
    ) -> None:
        """Publish scrubbed handoff context to one application-selected recipient.

        The historical method name is retained, but publication is not a call
        transfer or proof of receipt. The application must authorize the recipient
        and orchestrate the human connection through the provider's native APIs.
        This method never disconnects the room or marks the session transferred.
        Publication errors and cancellation propagate to the caller. A recipient
        leaving after validation remains a delivery risk requiring application ACKs.
        """
        session_id = self._publication_session(ctx, recipient_identity)
        payload = self.build_transfer_payload(session_id, reason=reason, scrub_pii=True)
        await _publish_transfer_data(ctx, payload, recipient_identity)
        logger.info("Handoff context publication submitted (session=%s)", session_id)

    # ------------------------------------------------------------------
    # Confidence gate entry point
    # ------------------------------------------------------------------

    async def on_confidence_low(
        self,
        ctx: Any,
        confidence_score: float,
        threshold: float,
        *,
        recipient_identity: str,
    ) -> None:
        """Record the escalation signal and publish context to a selected recipient.

        The caller determines that escalation is needed. Native orchestration must
        separately confirm a human connection; this hook does not end the call.
        Invalid destinations, consent, or sessions are rejected before state updates.
        """
        session_id = self._publication_session(ctx, recipient_identity)
        self._state_manager.update_state(
            session_id,
            lambda state: _set_escalation_fields(state, confidence_score, threshold),
        )
        await self.transfer(ctx, reason="confidence_escalation", recipient_identity=recipient_identity)


# ------------------------------------------------------------------
# Module-private helpers
# ------------------------------------------------------------------

def _room_name(ctx: Any) -> Optional[str]:
    """Extract room name from JobContext without raising on missing attributes."""
    try:
        return ctx.room.name
    except AttributeError:
        return None


def _set_escalation_fields(state: Any, score: float, threshold: float) -> None:
    """Updater function applied atomically to ConversationState before transfer."""
    state.escalation_trigger = "low_confidence"
    state.escalation_confidence_score = score
    state.platform_metadata["confidence_threshold"] = threshold
    state.platform_metadata["escalated_at"] = time.time()


async def _publish_transfer_data(ctx: Any, payload: Dict[str, Any], recipient_identity: str) -> None:
    """Submit one reliable packet; delivery and human acceptance are not guaranteed.

    Never broaden the destination or suppress SDK failures. The caller owns
    acknowledgment, retries, and connection orchestration.
    """
    raw = json.dumps(payload, default=str).encode()
    await ctx.room.local_participant.publish_data(
        raw,
        reliable=True,
        destination_identities=[recipient_identity],
        topic=_TRANSFER_CHANNEL,
    )
