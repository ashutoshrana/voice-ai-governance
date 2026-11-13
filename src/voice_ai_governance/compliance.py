"""
Compliance enforcement policies for voice AI pipelines.

Configurable checks of caller-supplied state, not legal certification.
See docs/POLICY_SEMANTICS.md for applicability, blocking, and migration.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

__all__ = [
    "CompliancePolicy",
    "HIPAAVoicePolicy",
    "FERPAVoicePolicy",
    "EUAIActVoicePolicy",
    "ComplianceViolation",
    "ComplianceCheckResult",
]


class ViolationSeverity(str, Enum):
    WARNING = "warning"
    VIOLATION = "violation"
    CRITICAL = "critical"


@dataclass
class ComplianceViolation:
    regulation: str
    rule_id: str
    description: str
    severity: ViolationSeverity
    recommended_action: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class ComplianceCheckResult:
    # A policy decision, not a legal-compliance certificate. Inspect violations too.
    passed: bool
    violations: List[ComplianceViolation] = field(default_factory=list)
    required_actions: List[str] = field(default_factory=list)
    audit_log: Dict[str, Any] = field(default_factory=dict)


class CompliancePolicy(ABC):
    """Base class for voice AI compliance policies."""

    @property
    @abstractmethod
    def regulation_name(self) -> str: ...

    @abstractmethod
    def check(self, context: Dict[str, Any]) -> ComplianceCheckResult: ...

    @abstractmethod
    def on_violation(self, violation: ComplianceViolation) -> None: ...


class HIPAAVoicePolicy(CompliancePolicy):
    """
    Configurable HIPAA-related safeguards for voice AI pipelines.

    Enforces:
    - Check named PHI fields and caller-reported scrubbing before transfer
    - Require caller-attested BAA verification when configured
    - Return an audit record for this check (not all PHI access events)
    - Configurable recording-consent safeguard, not a §164.522 requirement

    Does not scrub content or verify contracts itself. Applicability, minimum
    necessary disclosure, and de-identification require deployment review.

    OWASP Agentic AI Top 10: ASI-02 (Tool Misuse) — prevents PHI from
    being passed to unauthorized tools.
    """

    PHI_ENTITY_TYPES = {
        "name", "date_of_birth", "address", "phone", "fax", "email",
        "ssn", "medical_record_number", "health_plan_beneficiary_number",
        "account_number", "certificate_number", "vehicle_identifier",
        "device_identifier", "url", "ip_address", "biometric_identifier",
        "photo", "diagnosis_code", "medication",
    }

    def __init__(
        self,
        require_consent_for_recording: bool = True,
        require_baa_verification: bool = True,
        scrub_phi_before_transfer: bool = True,
        *,
        block_on_violation: bool = False,
    ):
        self.require_consent_for_recording = require_consent_for_recording
        self.require_baa_verification = require_baa_verification
        self.scrub_phi_before_transfer = scrub_phi_before_transfer
        self.block_on_violation = block_on_violation
        self._violations: List[ComplianceViolation] = []

    @property
    def regulation_name(self) -> str:
        return "HIPAA"

    def check(self, context: Dict[str, Any]) -> ComplianceCheckResult:
        violations = []
        required_actions = []

        # Explicit requirement: missing/non-boolean evidence cannot satisfy it.
        if self.require_baa_verification and context.get("baa_verified") is not True:
            violations.append(ComplianceViolation(
                regulation="HIPAA",
                rule_id="HIPAA-164.502-BAA-01",
                description="Configured BAA verification requirement lacks a true attestation",
                severity=ViolationSeverity.CRITICAL,
                recommended_action="Verify the applicable BAA before processing; supply baa_verified=True",
            ))
            required_actions.append("verify_baa")

        # Check 1: PHI in transfer payload without scrubbing
        if (self.scrub_phi_before_transfer and context.get("transfer_initiated")
                and context.get("phi_scrubbed") is not True):
            phi_fields = set(context.get("entities", {}).keys()) & self.PHI_ENTITY_TYPES
            if phi_fields:
                violations.append(ComplianceViolation(
                    regulation="HIPAA",
                    rule_id="HIPAA-164.514-01",
                    description=f"PHI fields in transfer payload without scrubbing: {phi_fields}",
                    severity=ViolationSeverity.CRITICAL,
                    recommended_action="Scrub PHI fields before warm transfer",
                ))
                required_actions.append("scrub_phi")

        # Check 2: Recording without consent
        if (self.require_consent_for_recording
                and context.get("recording_active")
                and context.get("consent_obtained") is not True):
            violations.append(ComplianceViolation(
                regulation="HIPAA",
                # Legacy identifier; §164.522 is NOT a recording-consent rule.
                rule_id="HIPAA-164.522-01",
                description="Configured recording-consent safeguard lacks documented consent",
                severity=ViolationSeverity.VIOLATION,
                recommended_action="Obtain verbal consent and log timestamp before recording",
            ))
            required_actions.append("obtain_consent")

        passed = not any(
            v.severity == ViolationSeverity.CRITICAL
            or (self.block_on_violation and v.severity == ViolationSeverity.VIOLATION)
            for v in violations
        )
        return ComplianceCheckResult(
            passed=passed,
            violations=violations,
            required_actions=required_actions,
            audit_log={
                "regulation": "HIPAA",
                "baa_verified": context.get("baa_verified"),
                "block_on_violation": self.block_on_violation,
                "context_keys_checked": list(context.keys()),
                "phi_fields_present": list(set(context.get("entities", {}).keys()) & self.PHI_ENTITY_TYPES),
                "timestamp": time.time(),
            },
        )

    def on_violation(self, violation: ComplianceViolation) -> None:
        self._violations.append(violation)


class FERPAVoicePolicy(CompliancePolicy):
    """
    FERPA (20 U.S.C. § 1232g) compliance enforcement for voice AI in higher education.

    Enforces:
    - Student education record access restriction (§ 99.31)
    - Directory information vs. non-directory information boundary
    - Third-party disclosure prevention
    - Legitimate educational interest verification
    - Student consent for disclosure (§ 99.30)

    Prevents voice agents from disclosing student records to unauthorized callers.
    """

    EDUCATION_RECORD_FIELDS = {
        "student_id", "gpa", "grades", "enrollment_status",
        "financial_aid_balance", "tuition_balance", "course_schedule",
        "degree_progress", "disciplinary_record", "counseling_notes",
    }

    DIRECTORY_INFORMATION_FIELDS = {
        "name", "major", "enrollment_status", "dates_of_attendance",
        "degrees_received", "participation_in_activities",
    }

    def __init__(
        self,
        directory_info_opt_out: bool = False,  # If True, student opted out of directory disclosure
        require_caller_identity_verification: bool = True,
    ):
        self.directory_info_opt_out = directory_info_opt_out
        self.require_caller_identity_verification = require_caller_identity_verification

    @property
    def regulation_name(self) -> str:
        return "FERPA"

    def check(self, context: Dict[str, Any]) -> ComplianceCheckResult:
        violations = []
        required_actions = []

        caller_verified = context.get("caller_identity_verified", False)
        caller_is_student = context.get("caller_is_student", False)
        entities = context.get("entities", {})

        # Check 1: Non-directory education records disclosed without identity verification
        non_directory = set(entities.keys()) & (
            self.EDUCATION_RECORD_FIELDS - self.DIRECTORY_INFORMATION_FIELDS
        )
        if non_directory and not caller_verified:
            violations.append(ComplianceViolation(
                regulation="FERPA",
                rule_id="FERPA-99.31-01",
                description=f"Education record fields in context without caller identity verification: {non_directory}",
                severity=ViolationSeverity.CRITICAL,
                recommended_action="Verify caller identity (student SSO, knowledge-based auth) before disclosing records",
            ))
            required_actions.append("verify_caller_identity")

        # Check 2: Directory information disclosed when student opted out
        if self.directory_info_opt_out:
            directory_fields = set(entities.keys()) & self.DIRECTORY_INFORMATION_FIELDS
            if directory_fields and not caller_is_student:
                violations.append(ComplianceViolation(
                    regulation="FERPA",
                    rule_id="FERPA-99.37-01",
                    description=f"Directory information disclosed despite student opt-out: {directory_fields}",
                    severity=ViolationSeverity.VIOLATION,
                    recommended_action="Do not disclose directory information; student has opted out",
                ))

        passed = all(v.severity != ViolationSeverity.CRITICAL for v in violations)
        return ComplianceCheckResult(
            passed=passed,
            violations=violations,
            required_actions=required_actions,
            audit_log={
                "regulation": "FERPA",
                "caller_verified": caller_verified,
                "education_record_fields_in_context": list(non_directory),
                "timestamp": time.time(),
            },
        )

    def on_violation(self, violation: ComplianceViolation) -> None:
        pass


class EUAIActVoicePolicy(CompliancePolicy):
    """
    EU AI Act Article 14 (Human Oversight) and Article 50(1) (AI-interaction disclosure)
    compliance enforcement for voice AI systems.

    Enforces:
    - Caller-attested human override availability for configured high-risk use
    - Caller-attested AI identity disclosure (Article 50(1))
    - Audit record of this check, not complete Article 12 logging

    High-risk status depends on Article 6 / Annex III intended use and exceptions,
    not a sector label. A transfer capability alone does not satisfy Article 14.

    These configurable checks use caller-supplied state, not an independent
    legal applicability determination or compliance certification. Article 50(1)
    includes exceptions; deployment obligations and timelines require review.
    """

    def __init__(
        self,
        require_ai_disclosure: bool = True,
        require_human_override_capability: bool = True,
        is_high_risk_context: bool = False,
        *,
        block_on_violation: bool = False,
    ):
        self.require_ai_disclosure = require_ai_disclosure
        self.require_human_override_capability = require_human_override_capability
        self.is_high_risk_context = is_high_risk_context
        self.block_on_violation = block_on_violation

    @property
    def regulation_name(self) -> str:
        return "EU AI Act"

    def check(self, context: Dict[str, Any]) -> ComplianceCheckResult:
        violations = []
        required_actions = []

        # Check 1: AI identity not disclosed to user (Article 50(1))
        if self.require_ai_disclosure and context.get("ai_identity_disclosed") is not True:
            violations.append(ComplianceViolation(
                regulation="EU AI Act",
                # Historical identifier retained for consumer compatibility.
                # The applicable disclosure reference is Article 50(1).
                rule_id="EUAIA-13.1-01",
                description="AI system has not disclosed its AI nature to the user (Article 50(1))",
                severity=ViolationSeverity.VIOLATION,
                recommended_action="Disclose AI identity at call start: 'You are speaking with an AI assistant'",
            ))
            required_actions.append("disclose_ai_identity")

        # Check 2: High-risk context without human oversight capability (Article 14.3)
        if (self.is_high_risk_context
                and self.require_human_override_capability
                and context.get("human_override_available") is not True):
            violations.append(ComplianceViolation(
                regulation="EU AI Act",
                rule_id="EUAIA-14.3-01",
                description="Configured high-risk context lacks a true human-override attestation (Article 14)",
                severity=ViolationSeverity.CRITICAL,
                recommended_action="Ensure warm transfer to human agent is available and tested",
            ))

        if any(v.rule_id == "EUAIA-14.3-01" for v in violations):
            required_actions.append("verify_human_override")
        passed = not any(
            v.severity == ViolationSeverity.CRITICAL
            or (self.block_on_violation and v.severity == ViolationSeverity.VIOLATION)
            for v in violations
        )
        return ComplianceCheckResult(
            passed=passed,
            violations=violations,
            required_actions=required_actions,
            audit_log={
                "regulation": "EU AI Act",
                "block_on_violation": self.block_on_violation,
                "ai_identity_disclosed": context.get("ai_identity_disclosed"),
                "human_override_available": context.get("human_override_available"),
                "high_risk_context": self.is_high_risk_context,
                "timestamp": time.time(),
            },
        )

    def on_violation(self, violation: ComplianceViolation) -> None:
        pass
