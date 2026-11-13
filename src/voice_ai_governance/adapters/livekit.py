"""
LiveKit Agents v1.8.7+ warm transfer adapter for voice-ai-governance.

Integrates TCPA consent gating, HIPAA compliance enforcement, PII scrubbing,
and structured warm transfer handoff into caller-managed LiveKit agent deployments.

Regulatory citations:
  - TCPA: 47 U.S.C. § 227 (consent required before autodialed/prerecorded calls)
  - HIPAA: 45 CFR § 164 (PHI minimum necessary, transfer payload scrubbing)
  - EU AI Act: Article 14 (human oversight capability for high-risk AI systems)

Install:
    pip install "voice-ai-governance[livekit]"

Usage:
    See docs/LIVEKIT.md for state registration, trusted consent/BAA evidence,
    targeted publication and the installed-SDK qualification boundary.

"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
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

# Consent fields must be literal JSON booleans, not truthy strings/numbers
_REQUIRED_CONSENT_FIELDS = ("consent_obtained",)

# Application data topic; not native SIP transfer signaling
_TRANSFER_CHANNEL = "voice_governance_transfer"
# https://docs.livekit.io/transport/data/packets/ (reliable user payload limit)
_MAX_RELIABLE_PAYLOAD_BYTES = 15 * 1024


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
            f"The configured consent safeguard requires a true attestation ({citation}); "
            "the application must determine the applicable consent requirements."
        )


class LiveKitWarmTransferAdapter:
    """Build and publish scrubbed handoff context to one selected room participant.

    The application authorizes the recipient and owns native call orchestration.
    Publication is not proof of receipt, human acceptance, or legal compliance.

    Args:
        state_manager: WarmTransferStateManager instance. If None, a stateless
            in-process instance is created (suitable for single-server deployments).
        hipaa_policy: HIPAAVoicePolicy instance. Defaults to policy with PHI
            scrubbing and BAA-attestation checks enabled on transfer. Supply trusted
            state.platform_metadata["baa_verified"]; inspect the returned audit.

    See docs/LIVEKIT.md for a complete registration/dispatch example. Creating
    an adapter alone does not register state or establish consent/BAA evidence.
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

        if not isinstance(metadata_str, str) or not metadata_str:
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
            "consent_obtained": parsed.get("consent_obtained") is True,
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
            if identity.get(field_name) is not True:
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
        Build an application handoff-context payload, not a native SIP request.

        Retrieves accumulated conversation state from WarmTransferStateManager,
        runs HIPAA compliance check on the payload context, and serialises the
        result to a dict for targeted LiveKit data publication. Native SIP
        transfer/consultation APIs and their schemas are separate.

        Args:
            session_id: Conversation session identifier previously registered
                with the state manager.
            reason: Transfer reason code forwarded to the contact center.
            scrub_pii: When True (default), PIIScrubber applies configured
                patterns to the payload. This is not a complete de-identification
                or minimum-necessary determination.

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

        state = self._state_manager.get_state(session_id)
        hipaa_context = {
            "baa_verified": state.platform_metadata.get("baa_verified") if state else None,
            "recording_active": state.platform_metadata.get("recording_active", False) if state else False,
            "transfer_initiated": True,
            "phi_scrubbed": handoff.pii_scrubbed,
            "entities": handoff.collected_entities,
            "consent_obtained": handoff.consent_obtained,
        }
        hipaa_result = self._hipaa_policy.check(hipaa_context)
        for violation in hipaa_result.violations:
            self._hipaa_policy.on_violation(violation)

        payload = handoff.to_dict()
        if payload.get("session_id", session_id) != session_id:
            # Preserve only canonical UUIDv4 correlation IDs from registered state.
            # Broad PII regexes can misclassify their digit groups as payment cards.
            try:
                parsed_id = uuid.UUID(session_id)
                canonical_id = parsed_id.version == 4 and str(parsed_id) == session_id
            except (ValueError, TypeError, AttributeError):
                canonical_id = False
            if state is None or state.session_id != session_id or not canonical_id:
                raise ValueError("Redaction changed a noncanonical session identifier; refusing ambiguous handoff")
            payload["session_id"] = session_id
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
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("A nonempty registered session ID string is required")
        state = self._state_manager.get_state(session_id)
        if state is None or state.status not in (TransferStatus.ACTIVE, TransferStatus.ESCALATING):
            raise ValueError("An active or escalating registered session is required")
        return session_id

    @staticmethod
    def _assert_hipaa_publication(payload: dict[str, Any]) -> None:
        """Reject absent or failing policy evidence before data leaves the room."""
        audit = payload.get("hipaa_audit")
        if not isinstance(audit, dict) or audit.get("passed") is not True:
            raise RuntimeError("HIPAA policy check did not pass; handoff context was not published")

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
        A missing/failing HIPAA decision raises RuntimeError before publication.
        Publication errors and cancellation propagate to the caller. A recipient
        leaving after validation remains a delivery risk requiring application ACKs.
        """
        session_id = self._publication_session(ctx, recipient_identity)
        payload = self.build_transfer_payload(session_id, reason=reason, scrub_pii=True)
        self._assert_hipaa_publication(payload)
        raw = _encode_transfer_payload(payload)
        await _publish_transfer_data(ctx, raw, recipient_identity)
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
        for name, value in (("confidence_score", confidence_score), ("threshold", threshold)):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not 0 <= value <= 1 or not math.isfinite(value)):
                raise ValueError(f"{name} must be a finite number between 0 and 1")
        session_id = self._publication_session(ctx, recipient_identity)
        payload = self.build_transfer_payload(session_id, reason="confidence_escalation")
        self._assert_hipaa_publication(payload)
        # Build one prospective packet before mutating state. Publish these exact
        # bytes, rather than rebuilding a potentially larger packet after update.
        payload["escalation_trigger"] = "low_confidence"
        payload["confidence_at_transfer"] = confidence_score
        payload["caller_summary"] += " Escalation signal: low confidence."
        raw = _encode_transfer_payload(payload)

        def record_if_eligible(state: Any) -> None:
            if state.status not in (TransferStatus.ACTIVE, TransferStatus.ESCALATING):
                raise ValueError("Session is no longer eligible for escalation")
            decision = self._hipaa_policy.check({
                "transfer_initiated": True,
                "phi_scrubbed": payload.get("pii_scrubbed", False),
                "entities": payload.get("collected_entities", {}),
                "baa_verified": state.platform_metadata.get("baa_verified"),
                "recording_active": state.platform_metadata.get("recording_active", False),
                "consent_obtained": state.consent_obtained,
            })
            if decision.passed is not True:
                raise RuntimeError("HIPAA policy check did not pass; escalation was not recorded")
            _set_escalation_fields(state, confidence_score, threshold)

        updated = self._state_manager.update_state(session_id, record_if_eligible)
        if updated is None:
            raise ValueError("Session disappeared before escalation could be recorded")
        # Preserve authorization rechecks without rebuilding the serialized packet.
        if self._publication_session(ctx, recipient_identity) != session_id:
            raise ValueError("Session changed before publication")
        self._assert_hipaa_publication(self.build_transfer_payload(session_id))
        await _publish_transfer_data(ctx, raw, recipient_identity)


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


def _encode_transfer_payload(payload: dict[str, Any]) -> bytes:
    """Reject oversize packets, never silently truncate governance context."""
    raw = json.dumps(payload, default=str, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(raw) > _MAX_RELIABLE_PAYLOAD_BYTES:
        raise ValueError(
            f"Handoff context exceeds the {_MAX_RELIABLE_PAYLOAD_BYTES}-byte reliable packet limit; "
            "use an application-managed authorized reference or reduce context before retrying"
        )
    return raw


async def _publish_transfer_data(ctx: Any, raw: bytes, recipient_identity: str) -> None:
    """Submit one reliable packet; delivery and human acceptance are not guaranteed.

    Never broaden the destination or suppress SDK failures. The caller owns
    acknowledgment, retries, and connection orchestration.
    """
    await ctx.room.local_participant.publish_data(
        raw,
        reliable=True,
        destination_identities=[recipient_identity],
        topic=_TRANSFER_CHANNEL,
    )
