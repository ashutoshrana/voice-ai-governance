"""
Amazon Connect warm transfer adapter for voice-ai-governance.

Integrates voice-ai-governance state management and compliance enforcement
with Amazon Connect Contact Lens, enabling compliant AI-to-human warm
transfers through Amazon Connect queues.

Installation
------------
    pip install voice-ai-governance boto3

Usage
-----
    from voice_ai_governance.adapters.amazon_connect import AmazonConnectAdapter
    from voice_ai_governance.state import WarmTransferStateManager

    manager = WarmTransferStateManager()
    adapter = AmazonConnectAdapter(
        instance_id="arn:aws:connect:us-east-1:123456789012:instance/abc",
        region_name="us-east-1",
        state_manager=manager,
    )

    # On escalation trigger:
    payload = adapter.build_transfer_payload(
        session_id=session_id,
        contact_id=contact_id,
        reason="confidence_escalation",
    )
    # Pass payload["attributes"] to Amazon Connect UpdateContactAttributes API
    # or into a SetAttributes block in your contact flow.

    caller = adapter.get_caller_info(contact_id)
    turns  = adapter.extract_transcript(contact_id)

Compliance Citations
--------------------
- TCPA: 47 U.S.C. § 227 — requires prior express written consent for
  autodialed or prerecorded calls to cell phones; voice AI outbound
  dialing must verify consent before initiating.
- HIPAA: 45 CFR § 164 (Privacy and Security Rules) — PHI must be
  scrubbed from transfer attributes before handoff to human agents;
  BAA required for Business Associate relationships.
- EU AI Act Art. 14 (Human Oversight) — high-risk AI voice systems must
  maintain the capability for human intervention; warm transfer to an
  agent satisfies the Art. 14.3 override requirement.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore[assignment]
    BotoCoreError = Exception  # type: ignore[assignment,misc]
    ClientError = Exception  # type: ignore[assignment,misc]

from voice_ai_governance.compliance import ComplianceCheckResult, HIPAAVoicePolicy
from voice_ai_governance.pii import PIIScrubber
from voice_ai_governance.state import WarmTransferStateManager

__all__ = [
    "AmazonConnectAdapter",
    "TCPAConsentError",
    "ContactLensUnavailableError",
    "SpeakerTurn",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TCPAConsentError(RuntimeError):
    """
    Raised when a warm transfer is attempted without documented TCPA consent.

    TCPA (47 U.S.C. § 227) requires prior express written consent before
    placing autodialed or prerecorded voice calls to wireless numbers.
    A transfer initiated by an AI voice system qualifies; consent must
    be captured in session state before this adapter will proceed.
    """


class ContactLensUnavailableError(RuntimeError):
    """
    Raised when Contact Lens transcript retrieval is attempted but the
    feature is not enabled on the Amazon Connect instance, or when
    boto3 is not installed.
    """


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpeakerTurn:
    """A single turn from a Contact Lens real-time transcript."""

    participant_role: str       # "AGENT", "CUSTOMER", or "SYSTEM"
    content: str
    begin_offset_millis: int
    end_offset_millis: int
    sentiment: Optional[str] = None   # "POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED"
    sentiment_score: Optional[float] = None  # normalised -1.0 → 1.0


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class AmazonConnectAdapter:
    """
    Amazon Connect warm transfer adapter with TCPA, HIPAA, and EU AI Act compliance.

    Wraps Amazon Connect APIs (describe_contact, update_contact_attributes)
    and Amazon Connect Contact Lens APIs (get_transcript) to produce
    compliant warm transfer payloads for Connect queue transfers.

    The payload produced by build_transfer_payload() maps directly onto
    Amazon Connect contact attributes, which are available in contact
    flows, Lambda functions, and agent CTI adapters after the transfer.

    Args:
        instance_id:   Amazon Connect instance ARN or short instance ID.
        region_name:   AWS region (default "us-east-1").
        state_manager: voice-ai-governance WarmTransferStateManager.
                       Falls back to an in-memory manager if None.
        hipaa_policy:  Pre-configured HIPAAVoicePolicy; a default
                       (scrub_phi_before_transfer=True) is used if None.
        pii_scrubber:  PIIScrubber used when state_manager does not have
                       its own scrubber configured.
    """

    def __init__(
        self,
        instance_id: str,
        region_name: str = "us-east-1",
        state_manager: Optional[WarmTransferStateManager] = None,
        hipaa_policy: Optional[HIPAAVoicePolicy] = None,
        pii_scrubber: Optional[PIIScrubber] = None,
    ) -> None:
        if boto3 is None:
            raise ImportError(
                "boto3 is required for AmazonConnectAdapter. "
                "Install it with: pip install boto3"
            )

        self.instance_id = instance_id
        self.region_name = region_name

        self._state_manager = state_manager or WarmTransferStateManager(
            pii_scrubber=pii_scrubber or PIIScrubber()
        )
        self._hipaa_policy = hipaa_policy or HIPAAVoicePolicy(
            require_consent_for_recording=True,
            require_baa_verification=False,  # BAA lives outside the runtime
            scrub_phi_before_transfer=True,
        )
        self._pii_scrubber = pii_scrubber or PIIScrubber()

        # Lazily initialised so tests can construct without real AWS creds
        self._connect_client: Any = None
        self._lens_client: Any = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def build_transfer_payload(
        self,
        session_id: str,
        contact_id: str,
        reason: str = "confidence_escalation",
        scrub_pii: bool = True,
    ) -> Dict[str, Any]:
        """
        Build an Amazon Connect warm transfer payload.

        Runs TCPA consent gate, HIPAA compliance check, and PII scrubbing
        before constructing the final attributes dict.  The returned dict
        has the following shape::

            {
                "transferType": "WARM",
                "contactId": "<contact_id>",
                "attributes": {
                    "ConversationSummary":  "<str>",
                    "CallerIntent":         "<str | empty>",
                    "Sentiment":            "<positive|neutral|negative>",
                    "ConsentObtained":      "true" | "false",
                    "ComplianceFlags":      "<comma-separated list>",
                    "PIIScrubbed":          "true" | "false",
                    "HIPAAViolations":      "<comma-separated list>",
                    "TransferReason":       "<str>",
                    "EscalationTrigger":    "<str>",
                    "ConfidenceScore":      "<float str>",
                    "CallDurationSeconds":  "<int str>",
                    "TurnCount":            "<int str>",
                    "TransferTimestamp":    "<epoch float str>",
                },
            }

        All attribute values are strings because Amazon Connect contact
        attributes are string-typed.

        Raises:
            TCPAConsentError: if session state shows consent_obtained=False.
            ValueError:       if session_id is not found in state manager.
        """
        state = self._state_manager.get_state(session_id)
        if state is None:
            raise ValueError(
                f"No session state found for session_id={session_id!r}. "
                "Call state_manager.create_session() before building a payload."
            )

        self._assert_tcpa_consent(state.__dict__)

        hipaa_result = self._hipaa_policy.check({
            "transfer_initiated": True,
            "phi_scrubbed": scrub_pii,
            "entities": {k: v.value for k, v in state.entities.items()},
            "consent_obtained": state.consent_obtained,
            "recording_active": state.platform_metadata.get("recording_active", False),
        })

        payload = self._state_manager.build_handoff_payload(
            session_id=session_id,
            reason=reason,
            scrub_pii=scrub_pii,
        )

        # build_handoff_payload returns None only if state is missing;
        # we already checked above, so this branch guards against races.
        if payload is None:
            raise ValueError(f"State for session_id={session_id!r} disappeared during payload build.")

        hipaa_violation_codes = [
            v.rule_id for v in hipaa_result.violations
        ]

        attributes: Dict[str, str] = {
            "ConversationSummary": payload.caller_summary or "",
            "CallerIntent": payload.primary_intent or "",
            "Sentiment": payload.sentiment or "neutral",
            "ConsentObtained": str(payload.consent_obtained).lower(),
            "ComplianceFlags": ",".join(payload.compliance_flags),
            "PIIScrubbed": str(payload.pii_scrubbed).lower(),
            "HIPAAViolations": ",".join(hipaa_violation_codes),
            "TransferReason": payload.transfer_reason,
            "EscalationTrigger": payload.escalation_trigger,
            "ConfidenceScore": f"{payload.confidence_at_transfer:.4f}",
            "CallDurationSeconds": str(int(payload.call_duration_seconds)),
            "TurnCount": str(payload.turn_count),
            "TransferTimestamp": str(payload.transfer_timestamp),
        }

        return {
            "transferType": "WARM",
            "contactId": contact_id,
            "attributes": attributes,
        }

    def get_caller_info(self, contact_id: str) -> Dict[str, Any]:
        """
        Retrieve caller ANI and contact metadata from Amazon Connect.

        Uses describe_contact to fetch the origination address (ANI),
        contact initiation method, channel, and queue ARN.

        Returns a dict::

            {
                "ani":        "+15550001234",   # or None if unavailable
                "contact_id": "<contact_id>",
                "metadata": {
                    "channel":            "VOICE",
                    "initiation_method":  "INBOUND",
                    "queue_arn":          "<arn>",
                    "agent_id":           "<id | None>",
                    "initiation_timestamp": "<ISO-8601>",
                },
            }

        Raises:
            RuntimeError: on AWS API errors.
        """
        client = self._get_connect_client()
        try:
            response = client.describe_contact(
                InstanceId=self.instance_id,
                ContactId=contact_id,
            )
        except ClientError as exc:
            raise RuntimeError(
                f"Amazon Connect describe_contact failed for contact_id={contact_id!r}: {exc}"
            ) from exc
        except BotoCoreError as exc:
            raise RuntimeError(
                f"AWS transport error fetching contact {contact_id!r}: {exc}"
            ) from exc

        contact = response.get("Contact", {})
        agent_info = contact.get("AgentInfo") or {}
        queue_info = contact.get("QueueInfo") or {}

        # CustomerEndpoint holds the caller's ANI for INBOUND voice calls
        customer_endpoint = contact.get("CustomerEndpoint") or {}
        ani = customer_endpoint.get("Address")

        return {
            "ani": ani,
            "contact_id": contact_id,
            "metadata": {
                "channel": contact.get("Channel"),
                "initiation_method": contact.get("InitiationMethod"),
                "queue_arn": queue_info.get("ARN"),
                "agent_id": agent_info.get("Id"),
                "initiation_timestamp": str(contact.get("InitiationTimestamp", "")),
            },
        }

    def extract_transcript(self, contact_id: str) -> List[SpeakerTurn]:
        """
        Extract real-time transcript turns from Amazon Connect Contact Lens.

        Contact Lens must be enabled on the instance and the contact must
        have been processed by real-time analytics for this to return data.

        Each SpeakerTurn contains participant role, text content, timing
        offsets, and per-turn sentiment from Contact Lens.

        Returns an empty list if no transcript segments are available
        (e.g., call in progress without buffered segments).

        Raises:
            ContactLensUnavailableError: if Contact Lens is not available
                or boto3 is absent.
            RuntimeError: on unrecoverable AWS API errors.
        """
        client = self._get_lens_client()

        try:
            response = client.list_realtime_contact_analysis_segments(
                InstanceId=self.instance_id,
                ContactId=contact_id,
            )
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("AccessDeniedException", "ResourceNotFoundException"):
                raise ContactLensUnavailableError(
                    f"Contact Lens is not enabled or accessible for contact "
                    f"{contact_id!r} on instance {self.instance_id!r}. "
                    "Enable Contact Lens in the Amazon Connect console and ensure "
                    "the IAM role has connect:ListRealtimeContactAnalysisSegments permission."
                ) from exc
            raise RuntimeError(
                f"Contact Lens API error for contact_id={contact_id!r}: {exc}"
            ) from exc
        except BotoCoreError as exc:
            raise RuntimeError(
                f"AWS transport error fetching Contact Lens segments for {contact_id!r}: {exc}"
            ) from exc

        turns: List[SpeakerTurn] = []
        for segment in response.get("Segments", []):
            transcript_seg = segment.get("Transcript")
            if not transcript_seg:
                continue

            sentiment_raw = transcript_seg.get("Sentiment", "NEUTRAL")
            sentiment_score = self._sentiment_to_score(sentiment_raw)

            turns.append(SpeakerTurn(
                participant_role=transcript_seg.get("ParticipantRole", "UNKNOWN"),
                content=transcript_seg.get("Content", ""),
                begin_offset_millis=transcript_seg.get("BeginOffsetMillis", 0),
                end_offset_millis=transcript_seg.get("EndOffsetMillis", 0),
                sentiment=sentiment_raw,
                sentiment_score=sentiment_score,
            ))

        # Segments are returned in reverse-chronological order by the API;
        # sort ascending so callers can iterate naturally.
        turns.sort(key=lambda t: t.begin_offset_millis)
        return turns

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assert_tcpa_consent(self, context: Dict[str, Any]) -> None:
        """
        Gate on TCPA prior express consent before any transfer is initiated.

        47 U.S.C. § 227(b)(1)(A) prohibits initiating calls to a cellular
        number using an ATDS or artificial/prerecorded voice without prior
        express consent.  Voice AI outbound and transferred calls fall in
        scope; consent must be affirmatively set and timestamped in session
        state by the application before calling this adapter.
        """
        if not context.get("consent_obtained", False):
            raise TCPAConsentError(
                "TCPA prior express consent has not been obtained for this session. "
                "Set state.consent_obtained=True and state.consent_timestamp before "
                "initiating a warm transfer (47 U.S.C. § 227)."
            )
        if not context.get("consent_timestamp"):
            raise TCPAConsentError(
                "TCPA consent timestamp is missing. "
                "Record state.consent_timestamp (Unix epoch float) at the moment "
                "the caller provides verbal consent (47 U.S.C. § 227)."
            )

    def _get_connect_client(self) -> Any:
        if self._connect_client is None:
            self._connect_client = boto3.client(
                "connect", region_name=self.region_name
            )
        return self._connect_client

    def _get_lens_client(self) -> Any:
        if self._lens_client is None:
            self._lens_client = boto3.client(
                "connect-contact-lens", region_name=self.region_name
            )
        return self._lens_client

    @staticmethod
    def _sentiment_to_score(sentiment: str) -> float:
        """
        Map Contact Lens sentiment labels to the [-1, 1] float scale used
        by ConversationState.sentiment_scores, enabling cross-platform
        sentiment aggregation without adapter-specific branches upstream.
        """
        return {
            "POSITIVE": 0.7,
            "NEGATIVE": -0.7,
            "MIXED": 0.0,
            "NEUTRAL": 0.0,
        }.get(sentiment.upper(), 0.0)
