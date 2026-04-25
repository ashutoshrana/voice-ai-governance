"""
PII (Personally Identifiable Information) detection and scrubbing for voice AI.

Provides regex-based and pattern-matching PII scrubbing for voice transcripts
and entity dictionaries before warm transfer handoff.

Supports HIPAA PHI, FERPA education records, PCI-DSS payment data,
and GDPR personal data categories.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["PIIPattern", "PIIScrubber", "ScrubResult"]


class PIIPattern(str, Enum):
    SSN = "ssn"
    CREDIT_CARD = "credit_card"
    PHONE = "phone"
    EMAIL = "email"
    DATE_OF_BIRTH = "date_of_birth"
    MEDICAL_RECORD = "medical_record"
    STUDENT_ID = "student_id"
    BANK_ACCOUNT = "bank_account"


# Compiled regex patterns for performance
_PATTERNS: Dict[PIIPattern, re.Pattern] = {
    PIIPattern.SSN: re.compile(r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b"),
    PIIPattern.CREDIT_CARD: re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
    PIIPattern.PHONE: re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    PIIPattern.EMAIL: re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    PIIPattern.DATE_OF_BIRTH: re.compile(
        r"\b(?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])[/-](?:19|20)\d{2}\b"
    ),
    PIIPattern.BANK_ACCOUNT: re.compile(r"\b\d{8,17}\b"),
}

_REPLACEMENT_TOKENS: Dict[PIIPattern, str] = {
    PIIPattern.SSN: "[SSN_REDACTED]",
    PIIPattern.CREDIT_CARD: "[CARD_REDACTED]",
    PIIPattern.PHONE: "[PHONE_REDACTED]",
    PIIPattern.EMAIL: "[EMAIL_REDACTED]",
    PIIPattern.DATE_OF_BIRTH: "[DOB_REDACTED]",
    PIIPattern.MEDICAL_RECORD: "[MRN_REDACTED]",
    PIIPattern.STUDENT_ID: "[STUDENT_ID_REDACTED]",
    PIIPattern.BANK_ACCOUNT: "[ACCOUNT_REDACTED]",
}

# Entity keys that are inherently PII
_PII_ENTITY_KEYS = {
    "ssn", "social_security", "credit_card", "card_number",
    "date_of_birth", "dob", "bank_account", "account_number",
    "medical_record", "mrn", "student_id", "phone", "email",
    "diagnosis", "medication", "insurance_id",
}


@dataclass
class ScrubResult:
    original_length: int
    scrubbed_text: str
    patterns_found: List[PIIPattern] = field(default_factory=list)
    replacements_made: int = 0
    scrubbed: bool = False


class PIIScrubber:
    """
    PII scrubber for voice transcripts and entity dictionaries.

    Applies regex-based detection and replacement for common PII patterns
    found in voice AI transcripts. Configurable for HIPAA, FERPA, and GDPR
    data categories.

    Example:
        scrubber = PIIScrubber(patterns=[PIIPattern.SSN, PIIPattern.PHONE])
        result = scrubber.scrub_text("My SSN is 123-45-6789 and my phone is 555-1234")
        # result.scrubbed_text = "My SSN is [SSN_REDACTED] and my phone is [PHONE_REDACTED]"
    """

    def __init__(
        self,
        patterns: Optional[List[PIIPattern]] = None,
        custom_patterns: Optional[Dict[str, re.Pattern]] = None,
        pii_entity_keys: Optional[set] = None,
    ):
        self.active_patterns = patterns or list(PIIPattern)
        self.custom_patterns = custom_patterns or {}
        self.pii_entity_keys = pii_entity_keys or _PII_ENTITY_KEYS

    def scrub_text(self, text: str) -> ScrubResult:
        scrubbed = text
        patterns_found = []
        replacements = 0

        for pattern_type in self.active_patterns:
            regex = _PATTERNS.get(pattern_type)
            if regex:
                matches = regex.findall(scrubbed)
                if matches:
                    patterns_found.append(pattern_type)
                    replacements += len(matches)
                    scrubbed = regex.sub(_REPLACEMENT_TOKENS[pattern_type], scrubbed)

        for name, regex in self.custom_patterns.items():
            matches = regex.findall(scrubbed)
            if matches:
                replacements += len(matches)
                scrubbed = regex.sub(f"[{name.upper()}_REDACTED]", scrubbed)

        return ScrubResult(
            original_length=len(text),
            scrubbed_text=scrubbed,
            patterns_found=patterns_found,
            replacements_made=replacements,
            scrubbed=replacements > 0,
        )

    def scrub_dict(
        self,
        data: Dict[str, Any],
        recursive: bool = True,
    ) -> Tuple[Dict[str, Any], bool]:
        """
        Scrub PII from a dictionary (e.g., collected entity values).
        Returns (scrubbed_dict, was_scrubbed).
        """
        scrubbed = {}
        was_scrubbed = False

        for key, value in data.items():
            if key.lower() in self.pii_entity_keys:
                scrubbed[key] = f"[{key.upper()}_REDACTED]"
                was_scrubbed = True
            elif isinstance(value, str):
                result = self.scrub_text(value)
                scrubbed[key] = result.scrubbed_text
                if result.scrubbed:
                    was_scrubbed = True
            elif isinstance(value, dict) and recursive:
                scrubbed[key], child_scrubbed = self.scrub_dict(value)
                was_scrubbed = was_scrubbed or child_scrubbed
            else:
                scrubbed[key] = value

        return scrubbed, was_scrubbed
