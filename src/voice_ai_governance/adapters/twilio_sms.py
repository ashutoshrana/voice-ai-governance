"""
Twilio SMS adapter for voice-ai-governance.

Builds TCPA-compliant outbound SMS messages, post-call case summary SMS,
and omnichannel warm transfer notifications. Integrates with Twilio
Programmable Messaging and Twilio Conversations APIs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from voice_ai_governance.pii import PIIScrubber
from voice_ai_governance.sms import (
    A2PCampaignConfig,
    ConsentType,
    OmnichannelConsentStore,
    OptOutHandler,
    SMSComplianceResult,
    SMSConsentRecord,
    TCPAPolicy,
)

__all__ = [
    "TwilioSMSMessage",
    "TwilioSMSAdapter",
    "PostCallSMSBuilder",
]

# PHI/PII URL parameter patterns — HIPAA requires scrubbing before SMS
_PHI_URL_PARAM_RE = re.compile(
    r"([?&](?:ssn|dob|date_of_birth|mrn|patient_id|acct|account_number|"
    r"member_id|npi|diagnosis|icd|rxn|ndc)=)[^&\s#]+",
    re.IGNORECASE,
)


def _scrub_url_params(url: str) -> str:
    """Replace PHI URL parameters with [REDACTED] to prevent PHI leakage in SMS links."""
    return _PHI_URL_PARAM_RE.sub(r"\1[REDACTED]", url)


@dataclass
class TwilioSMSMessage:
    """Represents a validated, compliance-checked outbound SMS message."""

    to: str
    from_: str
    body: str
    messaging_service_sid: Optional[str] = None
    status_callback: Optional[str] = None
    compliance_passed: bool = True
    compliance_violations: List[str] = field(default_factory=list)

    def to_twilio_params(self) -> Dict[str, Any]:
        params: Dict[str, Any] = {"to": self.to, "body": self.body}
        if self.from_:
            params["from_"] = self.from_
        if self.messaging_service_sid:
            params["messaging_service_sid"] = self.messaging_service_sid
        if self.status_callback:
            params["status_callback"] = self.status_callback
        return params


class TwilioSMSAdapter:
    """
    Twilio Programmable Messaging adapter with TCPA + HIPAA compliance.

    Validates consent, enforces quiet hours, scrubs PHI from message body
    and any embedded URLs before building the Twilio message params.

    Example::

        consent_store = OmnichannelConsentStore()
        adapter = TwilioSMSAdapter(
            from_number="+18005550100",
            consent_store=consent_store,
            campaign_config=A2PCampaignConfig(
                campaign_id="C1234",
                brand_name="Acme Health",
                campaign_type=A2PCampaignType.HEALTHCARE,
                use_case_description="AI appointment reminders",
            ),
        )

        message = adapter.build_message(
            to="+15551234567",
            body="Your appointment is confirmed. View details at https://portal.example.com/appt?patient_id=9876",
            message_type="transactional",
            recipient_hour=10,
        )

        if message.compliance_passed:
            twilio_client.messages.create(**message.to_twilio_params())
    """

    def __init__(
        self,
        from_number: str = "",
        consent_store: Optional[OmnichannelConsentStore] = None,
        tcpa_policy: Optional[TCPAPolicy] = None,
        campaign_config: Optional[A2PCampaignConfig] = None,
        scrub_phi: bool = True,
        messaging_service_sid: Optional[str] = None,
    ):
        self.from_number = from_number
        self.consent_store = consent_store or OmnichannelConsentStore()
        self.tcpa_policy = tcpa_policy or TCPAPolicy()
        self.campaign_config = campaign_config
        self.scrub_phi = scrub_phi
        self.messaging_service_sid = messaging_service_sid
        self._pii_scrubber = PIIScrubber()
        self._opt_out_handler = OptOutHandler()

    def process_inbound(self, from_number: str, body: str) -> SMSComplianceResult:
        """Process an inbound SMS — detect opt-out/help before routing to AI agent."""
        result = self._opt_out_handler.process(body, phone=from_number)
        if result.is_opt_out:
            self.consent_store.record_opt_out(
                from_number,
                campaign_id=self.campaign_config.campaign_id if self.campaign_config else None,
            )
        return result

    def build_message(
        self,
        to: str,
        body: str,
        message_type: str = "transactional",
        recipient_hour: Optional[int] = None,
        campaign_id: Optional[str] = None,
    ) -> TwilioSMSMessage:
        """Build a compliance-validated Twilio SMS message."""
        cid = campaign_id or (self.campaign_config.campaign_id if self.campaign_config else None)
        consent = self.consent_store.get_consent(to, campaign_id=cid)

        compliance = self.tcpa_policy.check(
            message_type=message_type,
            recipient_hour=recipient_hour,
            consent=consent,
        )

        if self.scrub_phi:
            # Scrub PHI URL params first, then scrub text-level PII
            body = _scrub_url_params(body)
            scrub_result = self._pii_scrubber.scrub_text(body)
            body = scrub_result.scrubbed_text

        # Always append opt-out footer for marketing messages
        if message_type == "marketing" and self.campaign_config:
            if self.campaign_config.opt_out_message not in body:
                body = f"{body}\n{self.campaign_config.opt_out_message}"

        return TwilioSMSMessage(
            to=to,
            from_=self.from_number,
            body=body,
            messaging_service_sid=self.messaging_service_sid,
            compliance_passed=compliance.passed,
            compliance_violations=compliance.violations,
        )


class PostCallSMSBuilder:
    """
    Builds a HIPAA/FERPA-compliant post-call case summary SMS.

    After a voice call escalation, sends a plain-text summary to the
    human agent or patient/student — with all PHI stripped from the body
    and replaced with a secure tokenized portal link.

    Example::

        builder = PostCallSMSBuilder(portal_base_url="https://portal.example.com/cases")
        message = builder.build(
            to_agent="+15551119999",
            from_number="+18005550100",
            caller_number="+15554445555",
            session_id="sess_abc123",
            intent="billing_inquiry",
            turn_count=6,
            escalation_reason="low_confidence",
            entities={"ssn": "123-45-6789", "topic": "billing"},  # SSN will be stripped
        )
        twilio_client.messages.create(**message.to_twilio_params())
    """

    def __init__(
        self,
        portal_base_url: str = "",
        scrub_phi: bool = True,
    ):
        self.portal_base_url = portal_base_url
        self.scrub_phi = scrub_phi
        self._pii_scrubber = PIIScrubber()

    def build(
        self,
        to_agent: str,
        from_number: str,
        caller_number: str,
        session_id: str,
        intent: str = "",
        turn_count: int = 0,
        escalation_reason: str = "",
        entities: Optional[Dict[str, Any]] = None,
        summary_text: Optional[str] = None,
    ) -> TwilioSMSMessage:
        caller_display = f"...{caller_number[-4:]}" if len(caller_number) >= 4 else "Unknown"
        intent_display = intent.replace("_", " ").title() if intent else "General"
        reason_display = escalation_reason.replace("_", " ").title() if escalation_reason else "Escalated"

        if summary_text:
            body = summary_text
        else:
            body = (
                f"[CASE ALERT] New inbound case\n"
                f"Caller: {caller_display}\n"
                f"Intent: {intent_display} | Turns: {turn_count}\n"
                f"Reason: {reason_display}"
            )

            if self.portal_base_url:
                body += f"\nDetails: {self.portal_base_url}/{session_id}"

        if self.scrub_phi:
            body = _scrub_url_params(body)
            scrub_result = self._pii_scrubber.scrub_text(body)
            body = scrub_result.scrubbed_text

        return TwilioSMSMessage(
            to=to_agent,
            from_=from_number,
            body=body,
            compliance_passed=True,
        )
