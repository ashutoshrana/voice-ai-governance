"""
LiveKit Agents v1.5.7 warm transfer adapter for voice-ai-governance.

Integrates TCPA consent gating, HIPAA compliance enforcement, PII scrubbing,
and structured warm transfer handoff into LiveKit Agents VoicePipelineAgent
deployments.

Regulatory citations:
  - TCPA: 47 U.S.C. § 227 (consent required before autodialed/prerecorded calls)
  - HIPAA: 45 CFR § 164 (PHI minimum necessary, transfer payload scrubbing)
  - EU AI Act: Article 14 (human oversight capability for high-risk AI systems)

Install:
    pip install livekit-agents>=1.5.7 voice-ai-governance

Usage:
    from livekit.agents import JobContext, WorkerOptions, cli
    from voice_ai_governance.adapters.livekit import LiveKitWarmTransferAdapter

    adapter = LiveKitWarmTransferAdapter()

    async def entrypoint(ctx: JobContext):
        await ctx.connect()
        # On confidence gate trigger:
        await adapter.on_confidence_low(ctx, confidence_score=0.42, threshold=0.65)

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
from voice_ai_governance.state import WarmTransferStateManager

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
    """
    Raised when a LiveKit job is entered without documented TCPA prior express consent.

    The Telephone Consumer Protection Act (47 U.S.C. § 227) requires written prior
    express consent before initiating an autodialed or prerecorded voice call. This
    error surfaces at the agent entrypoint so the session is rejected before any
    voice processing begins, preventing the enterprise from incurring per-call
    exposure.

    Catching callers should disconnect the room and log the event for compliance audit.
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
    """
    LiveKit Agents v1.5.7 warm transfer adapter with regulatory compliance.

    Wraps WarmTransferStateManager and HIPAAVoicePolicy to produce a structured
    warm transfer payload delivered via LiveKit data channel (reliable unicast)
    when the confidence gate or TCPA consent gate fires.

    The transfer payload mirrors the HandoffPayload schema used by the Twilio
    adapter, allowing the same downstream contact center routing logic to handle
    transfers from both platforms.

    EU AI Act Article 14 obligation: this adapter constitutes the human-oversight
    intervention mechanism required for high-risk AI voice deployments. The
    ``transfer`` method is the override/intervene path mandated by Art. 14.3(c).

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
            await adapter.on_confidence_low(ctx, confidence_score=0.41, threshold=0.65)
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

    async def transfer(
        self,
        ctx: Any,
        reason: str = "confidence_escalation",
    ) -> None:
        """
        Execute a warm transfer from the LiveKit room to a human agent.

        Sequence:
        1. Assert TCPA consent from job metadata (raises TCPAConsentError on failure).
        2. Look up or create a session in the state manager.
        3. Build and HIPAA-check the transfer payload.
        4. Publish the payload to the LiveKit room data channel (reliable unicast
           to all remote participants or a target SIP participant).
        5. Mark the session as transferred in state manager.
        6. Disconnect the room so LiveKit routes the SIP call to the PSTN trunk.

        The data channel publish (step 4) serves as the machine-readable handoff
        record required by EU AI Act Article 14 for audit; the room disconnect
        (step 6) is the actual human-override action.

        Args:
            ctx: LiveKit JobContext. May be None when running in test harness
                 without a real LiveKit connection; the method degrades gracefully.
            reason: Transfer reason forwarded to the contact center routing system.

        Raises:
            TCPAConsentError: If prior express consent is not documented in
                ``ctx.job.metadata``.
            RuntimeError: If LiveKit SDK is not installed.
        """
        if not _LIVEKIT_AVAILABLE:
            raise RuntimeError(
                "livekit-agents is not installed. "
                "Run: pip install 'livekit-agents>=1.5.7'"
            )

        if ctx is None:
            logger.warning("transfer() called with ctx=None; skipping LiveKit operations")
            return

        metadata_str = getattr(getattr(ctx, "job", None), "metadata", None)
        identity = self.extract_caller_identity(metadata_str)
        self._assert_tcpa_consent(identity)

        session_id = identity["session_id"]
        if session_id is None:
            session_id = self._state_manager.create_session(
                platform_metadata={"livekit_room": _room_name(ctx)}
            )

        payload = self.build_transfer_payload(
            session_id=session_id,
            reason=reason,
            scrub_pii=True,
        )

        self._state_manager.initiate_transfer(session_id)

        await _publish_transfer_data(ctx, payload)

        try:
            await ctx.room.disconnect()
        except Exception as exc:
            logger.error(
                "LiveKit room.disconnect() failed during warm transfer "
                "(session=%s, reason=%s): %s",
                session_id,
                reason,
                exc,
            )
            raise RuntimeError(
                f"Warm transfer room disconnect failed: {exc}"
            ) from exc

        logger.info(
            "Warm transfer complete (session=%s, reason=%s, hipaa_passed=%s)",
            session_id,
            reason,
            payload.get("hipaa_audit", {}).get("passed"),
        )

    # ------------------------------------------------------------------
    # Confidence gate entry point
    # ------------------------------------------------------------------

    async def on_confidence_low(
        self,
        ctx: Any,
        confidence_score: float,
        threshold: float,
    ) -> None:
        """
        Called by the confidence gate when agent certainty drops below threshold.

        This is the primary integration hook for VoicePipelineAgent deployments.
        Attach it to your confidence evaluator callback; the method handles the
        full transfer lifecycle including consent verification, HIPAA scrubbing,
        and LiveKit room handoff.

        EU AI Act Article 14.3(c) requires high-risk AI systems to support human
        override "whenever" the system's outputs may be inaccurate or unsafe. A
        sub-threshold confidence score is the operative "whenever" signal here.

        Args:
            ctx: LiveKit JobContext passed through from the agent entrypoint.
            confidence_score: Composite confidence score at trigger time [0.0, 1.0].
            threshold: The threshold value that was breached, forwarded to the
                transfer payload for contact center routing logic.

        Raises:
            TCPAConsentError: Propagated from ``transfer()`` without modification
                so callers can catch it and cleanly reject the session.
        """
        logger.info(
            "Confidence gate fired (score=%.3f < threshold=%.3f); initiating warm transfer",
            confidence_score,
            threshold,
        )

        metadata_str = getattr(getattr(ctx, "job", None), "metadata", None)
        identity = self.extract_caller_identity(metadata_str)
        session_id = identity.get("session_id")

        if session_id and self._state_manager.get_state(session_id):
            self._state_manager.update_state(
                session_id,
                lambda state: _set_escalation_fields(
                    state, confidence_score, threshold
                ),
            )

        await self.transfer(ctx, reason="confidence_escalation")


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


async def _publish_transfer_data(ctx: Any, payload: Dict[str, Any]) -> None:
    """
    Publish warm transfer payload to the LiveKit room data channel.

    Uses reliable data packets (SCTP ordered delivery) so the payload is
    guaranteed to arrive before the room disconnect triggers SIP re-routing.
    Targets all remote participants rather than a specific identity because the
    SIP trunk participant identity is assigned by the LiveKit SIP service and
    not known deterministically at transfer time.

    Fails silently with an error log rather than raising, because a failed
    data-channel publish should not prevent the room disconnect — the transfer
    must proceed even if the handoff record delivery fails.
    """
    try:
        raw = json.dumps(payload, default=str).encode()
        room = ctx.room

        destination_identities = list(room.remote_participants.keys()) or None

        await room.local_participant.publish_data(
            raw,
            reliable=True,
            destination_identities=destination_identities,
            topic=_TRANSFER_CHANNEL,
        )
    except AttributeError as exc:
        # publish_data signature changed between LiveKit SDK versions; log and
        # continue so the room disconnect is not blocked.
        logger.error(
            "publish_data attribute error — check livekit-agents version "
            ">= 1.5.7 is installed: %s",
            exc,
        )
    except Exception as exc:
        logger.error(
            "Failed to publish warm transfer payload over LiveKit data channel: %s",
            exc,
        )
