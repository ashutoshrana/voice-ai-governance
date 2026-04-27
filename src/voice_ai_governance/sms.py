"""
SMS compliance enforcement for AI-generated messaging.

Covers TCPA quiet hours, prior express written consent (PEWC), opt-out
interception (STOP/HELP/UNSUBSCRIBE), A2P 10DLC campaign validation,
HIPAA/FERPA transmission restrictions, and omnichannel consent bridging
from voice sessions.

No required dependencies — drop-in for any SMS pipeline.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

__all__ = [
    "ConsentType",
    "SMSConsentRecord",
    "TCPAPolicy",
    "A2PCampaignType",
    "A2PCampaignConfig",
    "A2PCampaignValidator",
    "OptOutHandler",
    "OmnichannelConsentStore",
    "SMSComplianceResult",
]

# TCPA quiet hours: no AI SMS outside 8am–9pm recipient local time
TCPA_QUIET_HOURS_START_HOUR = 21   # 9pm
TCPA_QUIET_HOURS_END_HOUR = 8      # 8am

# CTIA mandatory opt-out keywords (case-insensitive)
OPT_OUT_KEYWORDS: Set[str] = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
HELP_KEYWORDS: Set[str] = {"HELP", "INFO"}


class ConsentType(str, Enum):
    PRIOR_EXPRESS_WRITTEN = "prior_express_written"  # required for AI marketing SMS
    PRIOR_EXPRESS = "prior_express"                  # sufficient for transactional
    IMPLIED = "implied"                              # expiring; not sufficient for AI
    NONE = "none"


@dataclass
class SMSConsentRecord:
    phone_number: str
    consent_type: ConsentType
    timestamp: str
    channel: str = "sms"               # "sms" | "voice" | "web" (where consent was captured)
    opted_out: bool = False
    opt_out_timestamp: Optional[str] = None
    campaign_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_valid_for_ai_marketing(self) -> bool:
        return (
            not self.opted_out
            and self.consent_type == ConsentType.PRIOR_EXPRESS_WRITTEN
        )

    def is_valid_for_transactional(self) -> bool:
        return (
            not self.opted_out
            and self.consent_type in (
                ConsentType.PRIOR_EXPRESS_WRITTEN,
                ConsentType.PRIOR_EXPRESS,
            )
        )


@dataclass
class SMSComplianceResult:
    passed: bool
    violations: List[str] = field(default_factory=list)
    required_actions: List[str] = field(default_factory=list)
    is_opt_out: bool = False
    is_help_request: bool = False


class TCPAPolicy:
    """
    Enforces TCPA compliance for AI-generated SMS.

    Checks:
    - Quiet hours (8am–9pm recipient local time)
    - Prior Express Written Consent (PEWC) for marketing
    - Prior Express Consent for transactional
    - Opt-out state

    Example::

        policy = TCPAPolicy(require_pewc_for_marketing=True)
        result = policy.check(
            message_type="marketing",
            recipient_hour=22,  # 10pm — quiet hours violation
            consent=consent_record,
        )
        if not result.passed:
            print(result.violations)
    """

    def __init__(
        self,
        require_pewc_for_marketing: bool = True,
        require_consent_for_transactional: bool = True,
        enforce_quiet_hours: bool = True,
        quiet_start_hour: int = TCPA_QUIET_HOURS_START_HOUR,
        quiet_end_hour: int = TCPA_QUIET_HOURS_END_HOUR,
    ):
        self.require_pewc_for_marketing = require_pewc_for_marketing
        self.require_consent_for_transactional = require_consent_for_transactional
        self.enforce_quiet_hours = enforce_quiet_hours
        self.quiet_start_hour = quiet_start_hour
        self.quiet_end_hour = quiet_end_hour

    def _is_quiet_hours(self, recipient_hour: int) -> bool:
        if self.quiet_start_hour > self.quiet_end_hour:
            # Crosses midnight (e.g., 21–8)
            return recipient_hour >= self.quiet_start_hour or recipient_hour < self.quiet_end_hour
        return self.quiet_end_hour <= recipient_hour < self.quiet_start_hour

    def check(
        self,
        message_type: str = "transactional",
        recipient_hour: Optional[int] = None,
        consent: Optional[SMSConsentRecord] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> SMSComplianceResult:
        violations: List[str] = []
        required_actions: List[str] = []

        # Quiet hours check
        if self.enforce_quiet_hours and recipient_hour is not None:
            if self._is_quiet_hours(recipient_hour):
                violations.append(
                    f"TCPA-QUIET-HOURS: Recipient local hour {recipient_hour} "
                    f"is outside allowed window ({self.quiet_end_hour}am–{self.quiet_start_hour % 12 or 12}pm)"
                )
                required_actions.append("delay_until_quiet_hours_end")

        # Consent check
        if consent and consent.opted_out:
            violations.append(f"TCPA-OPT-OUT: Recipient {consent.phone_number} has opted out")
            required_actions.append("suppress_message")

        elif message_type == "marketing" and self.require_pewc_for_marketing:
            if consent is None or not consent.is_valid_for_ai_marketing():
                violations.append(
                    "TCPA-PEWC-01: AI marketing SMS requires Prior Express Written Consent — not obtained"
                )
                required_actions.append("obtain_pewc_before_sending")

        elif message_type == "transactional" and self.require_consent_for_transactional:
            if consent is None or not consent.is_valid_for_transactional():
                violations.append(
                    "TCPA-CONSENT-01: Transactional SMS requires Prior Express Consent"
                )
                required_actions.append("obtain_consent_before_sending")

        return SMSComplianceResult(
            passed=len(violations) == 0,
            violations=violations,
            required_actions=required_actions,
        )


class A2PCampaignType(str, Enum):
    MARKETING = "marketing"
    TRANSACTIONAL = "transactional"
    HEALTHCARE = "healthcare"
    EMERGENCY = "emergency"
    TWO_FACTOR = "2fa"
    EDUCATION = "education"
    CUSTOMER_CARE = "customer_care"
    MIXED = "mixed"


@dataclass
class A2PCampaignConfig:
    campaign_id: str
    brand_name: str
    campaign_type: A2PCampaignType
    use_case_description: str
    ai_generated: bool = True                 # Must be True for AI SMS agents
    opt_in_message: str = ""
    opt_out_message: str = "Reply STOP to unsubscribe. Msg&data rates may apply."
    help_message: str = ""
    max_messages_per_day: Optional[int] = None
    regulated_content: bool = False           # HIPAA/FERPA content involved
    has_embedded_links: bool = False


class A2PCampaignValidator:
    """
    Validates A2P 10DLC campaign configuration before sending.

    Enforces TCR (The Campaign Registry) requirements and carrier
    content policies for AI-generated business SMS.

    Example::

        config = A2PCampaignConfig(
            campaign_id="C1234",
            brand_name="Acme Health",
            campaign_type=A2PCampaignType.HEALTHCARE,
            use_case_description="AI appointment reminders",
            ai_generated=True,
            regulated_content=True,
        )
        validator = A2PCampaignValidator()
        result = validator.validate(config)
    """

    REGULATED_TYPES = {A2PCampaignType.HEALTHCARE, A2PCampaignType.EDUCATION}

    def validate(self, config: A2PCampaignConfig) -> SMSComplianceResult:
        violations: List[str] = []
        required_actions: List[str] = []

        if not config.opt_out_message:
            violations.append("A2P-CTIA-01: Opt-out message must be included in campaign config")
            required_actions.append("add_opt_out_message")

        if config.ai_generated and "AI" not in config.use_case_description.upper():
            violations.append(
                "A2P-TCR-01: AI-generated SMS campaigns must disclose AI in use-case description"
            )
            required_actions.append("add_ai_disclosure_to_description")

        if config.regulated_content and config.campaign_type not in self.REGULATED_TYPES:
            violations.append(
                f"A2P-REG-01: Regulated content (HIPAA/FERPA) should use HEALTHCARE or EDUCATION campaign type, "
                f"not {config.campaign_type.value}"
            )
            required_actions.append("update_campaign_type")

        if config.campaign_type == A2PCampaignType.MIXED:
            violations.append(
                "A2P-THROUGHPUT-01: MIXED campaign type has lowest throughput; "
                "use a dedicated type for regulated-sector AI agents"
            )
            required_actions.append("change_to_dedicated_campaign_type")

        return SMSComplianceResult(
            passed=len(violations) == 0,
            violations=violations,
            required_actions=required_actions,
        )


class OptOutHandler:
    """
    Intercepts inbound opt-out and help keywords.

    Must be called on every inbound SMS before routing to AI agent.
    Updates consent store and returns an action directive.

    Example::

        handler = OptOutHandler()
        result = handler.process("STOP", phone="+15551234567")
        if result.is_opt_out:
            consent_store.record_opt_out("+15551234567")
            sms_api.send("+15551234567", handler.opt_out_reply)
    """

    DEFAULT_OPT_OUT_REPLY = (
        "You have been unsubscribed. You will receive no further messages from us. "
        "Reply HELP for help or START to re-subscribe."
    )
    DEFAULT_HELP_REPLY = (
        "For help, visit our website or call us. "
        "Reply STOP to unsubscribe. Msg&data rates may apply."
    )

    def __init__(
        self,
        opt_out_reply: Optional[str] = None,
        help_reply: Optional[str] = None,
        additional_opt_out_keywords: Optional[Set[str]] = None,
    ):
        self.opt_out_reply = opt_out_reply or self.DEFAULT_OPT_OUT_REPLY
        self.help_reply = help_reply or self.DEFAULT_HELP_REPLY
        self._opt_out_keywords = OPT_OUT_KEYWORDS | (additional_opt_out_keywords or set())

    def process(self, message_body: str, phone: str = "") -> SMSComplianceResult:
        normalized = message_body.strip().upper()

        if normalized in self._opt_out_keywords:
            return SMSComplianceResult(
                passed=True,
                is_opt_out=True,
                required_actions=["record_opt_out", "send_opt_out_confirmation", "suppress_future_messages"],
            )

        if normalized in HELP_KEYWORDS:
            return SMSComplianceResult(
                passed=True,
                is_help_request=True,
                required_actions=["send_help_response"],
            )

        return SMSComplianceResult(passed=True)


class OmnichannelConsentStore:
    """
    Bridges consent records across voice and SMS channels.

    When a caller consents to follow-up SMS during a voice interaction,
    that consent propagates to the SMS channel without requiring a
    second opt-in. Backed by in-memory dict (swap for Redis in production).

    Example::

        store = OmnichannelConsentStore()

        # Record consent from voice session
        store.record_consent(
            phone="+15551234567",
            consent_type=ConsentType.PRIOR_EXPRESS_WRITTEN,
            channel="voice",
            campaign_id="C1234",
        )

        # Check consent before sending SMS
        record = store.get_consent("+15551234567", campaign_id="C1234")
        if record and record.is_valid_for_transactional():
            sms_api.send(...)
    """

    def __init__(self, backend: Optional[Any] = None):
        self._backend = backend
        self._store: Dict[str, SMSConsentRecord] = {}

    def _key(self, phone: str, campaign_id: Optional[str] = None) -> str:
        return f"{phone}:{campaign_id or 'default'}"

    def record_consent(
        self,
        phone: str,
        consent_type: ConsentType,
        channel: str = "voice",
        campaign_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SMSConsentRecord:
        record = SMSConsentRecord(
            phone_number=phone,
            consent_type=consent_type,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            channel=channel,
            campaign_id=campaign_id,
            metadata=metadata or {},
        )
        key = self._key(phone, campaign_id)

        if self._backend:
            self._backend.set(key, record.__dict__)
        else:
            self._store[key] = record

        return record

    def record_opt_out(self, phone: str, campaign_id: Optional[str] = None) -> None:
        key = self._key(phone, campaign_id)
        record = self.get_consent(phone, campaign_id)
        if record:
            record.opted_out = True
            record.opt_out_timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
            if self._backend:
                self._backend.set(key, record.__dict__)
            else:
                self._store[key] = record
        else:
            self._store[key] = SMSConsentRecord(
                phone_number=phone,
                consent_type=ConsentType.NONE,
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                opted_out=True,
                opt_out_timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                campaign_id=campaign_id,
            )

    def get_consent(
        self, phone: str, campaign_id: Optional[str] = None
    ) -> Optional[SMSConsentRecord]:
        key = self._key(phone, campaign_id)
        if self._backend:
            data = self._backend.get(key)
            if data:
                return SMSConsentRecord(**data)
            return None
        return self._store.get(key)

    def is_opted_out(self, phone: str, campaign_id: Optional[str] = None) -> bool:
        record = self.get_consent(phone, campaign_id)
        return record is not None and record.opted_out
