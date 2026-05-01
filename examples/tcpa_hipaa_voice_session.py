"""
Voice AI compliance example: TCPA + HIPAA in one pipeline.

Demonstrates TCPA consent checking, HIPAA PII scrubbing,
and warm transfer state preservation.

Install:
    pip install voice-ai-governance

Usage:
    python examples/tcpa_hipaa_voice_session.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Stub: TCPA consent store
# ---------------------------------------------------------------------------

_consent_store: dict[str, dict[str, Any]] = {
    "+15551234567": {
        "opted_in": True,
        "opted_in_at": "2026-03-01T10:00:00Z",
        "channel": "voice",
    }
}


def check_tcpa_consent(phone_number: str) -> dict[str, Any]:
    """
    Check TCPA consent for a phone number.

    Regulatory reference: TCPA 47 U.S.C. § 227(b)(1)(A) — prior express
    written consent required for automated calls/texts to mobile numbers.
    """
    record = _consent_store.get(phone_number)
    if not record:
        return {"allowed": False, "reason": "No TCPA consent on file"}
    if not record.get("opted_in"):
        return {"allowed": False, "reason": "Consumer has opted out (TCPA § 227)"}
    return {"allowed": True, "consented_at": record["opted_in_at"]}


# ---------------------------------------------------------------------------
# Stub: HIPAA PHI scrubber (18 Safe Harbor identifiers)
# ---------------------------------------------------------------------------

_PHI_PATTERNS = [
    (r"\b\d{3}-\d{2}-\d{4}\b", "[SSN REDACTED]"),          # SSN
    (r"\b\d{10,11}\b", "[MRN REDACTED]"),                     # Medical record numbers
    (r"\b[A-Z]{1,2}\d{6,8}\b", "[POLICY REDACTED]"),         # Insurance policy numbers
    (r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", "[DOB REDACTED]"),    # Dates
]

def scrub_phi(text: str) -> str:
    """
    Scrub PHI from text per HIPAA Safe Harbor method.

    Regulatory reference: HIPAA 45 CFR § 164.514(b) — Safe Harbor de-identification.
    """
    for pattern, replacement in _PHI_PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text


# ---------------------------------------------------------------------------
# Warm transfer state
# ---------------------------------------------------------------------------

_transfer_state: dict[str, Any] = {}

def save_transfer_state(session_id: str, context: dict[str, Any]) -> None:
    """
    Persist session context for warm transfer to human agent.

    Ensures the receiving agent has full context without requiring
    the customer to repeat information.
    """
    _transfer_state[session_id] = {
        **context,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    print(f"  Warm transfer state saved for session {session_id}")


def get_transfer_state(session_id: str) -> dict[str, Any] | None:
    return _transfer_state.get(session_id)


# ---------------------------------------------------------------------------
# EU AI Act Article 13: AI disclosure
# ---------------------------------------------------------------------------

def get_ai_disclosure_message() -> str:
    """
    EU AI Act Article 13 §1: Users must be informed they are interacting
    with an AI system.
    """
    return (
        "This call is handled by an AI assistant. "
        "You may request to speak with a human agent at any time."
    )


# ---------------------------------------------------------------------------
# Voice session simulation
# ---------------------------------------------------------------------------

def run_compliant_voice_session(
    phone_number: str,
    session_id: str,
    transcript: str,
) -> None:
    print(f"\n{'='*60}")
    print(f"Session: {session_id} | Caller: {phone_number}")
    print(f"{'='*60}")

    # Step 1: TCPA consent check
    consent = check_tcpa_consent(phone_number)
    if not consent["allowed"]:
        print(f"  BLOCKED: {consent['reason']}")
        return
    print(f"  ✅ TCPA consent verified (opted in: {consent['consented_at']})")

    # Step 2: EU AI Act Article 13 disclosure
    disclosure = get_ai_disclosure_message()
    print(f"  📢 AI Disclosure: {disclosure}")

    # Step 3: Process transcript, scrub PHI for logging
    scrubbed = scrub_phi(transcript)
    print(f"  Original: {transcript}")
    print(f"  Scrubbed: {scrubbed}")

    # Step 4: Simulate low ASR confidence → warm transfer
    asr_confidence = 0.61  # Below 0.70 threshold
    if asr_confidence < 0.70:
        print(f"  ⚠️  ASR confidence {asr_confidence:.2f} < 0.70 → warm transfer")
        save_transfer_state(session_id, {
            "phone_number": phone_number,
            "scrubbed_transcript": scrubbed,
            "asr_confidence": asr_confidence,
            "transfer_reason": "Low ASR confidence",
        })
        print(f"  → Human agent receives context: "
              f"{json.dumps(get_transfer_state(session_id), indent=2)}")
    else:
        print(f"  ✅ Confidence {asr_confidence:.2f} — AI continues session")


if __name__ == "__main__":
    # Consented caller with PHI in transcript
    run_compliant_voice_session(
        phone_number="+15551234567",
        session_id="voice-sess-001",
        transcript=(
            "My DOB is 3/15/1982 and my insurance is BC123456. "
            "I need to reschedule my appointment."
        ),
    )

    # Non-consented caller — blocked
    run_compliant_voice_session(
        phone_number="+15559999999",
        session_id="voice-sess-002",
        transcript="Hello, I want to check my account balance.",
    )
