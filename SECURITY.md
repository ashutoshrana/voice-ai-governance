# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.2.x   | Yes       |
| 0.1.x   | No        |

## Reporting a Vulnerability

**Do not report security vulnerabilities through public GitHub issues.**

Use the [GitHub Security Advisory](../../security/advisories/new) feature,
or email the maintainer directly. Response within 72 hours.

## Scope

This library implements compliance enforcement middleware for voice AI pipelines.
The security surface includes:

- **PII scrubbing bypass**: Inputs engineered to evade PHI/FERPA pattern detection
  in the scrubbing layer before post-call summaries or logs
- **TCPA consent spoofing**: Manipulation of consent state to bypass TCPA
  quiet-hours or opt-out enforcement
- **Warm transfer state tampering**: Modification of Redis-backed transfer
  state to inject unauthorized context into receiving agents
- **Confidence gate bypass**: Adversarial audio/text designed to produce
  artificially high ASR confidence scores to avoid human escalation
- **A2P 10DLC disclosure suppression**: Techniques to prevent AI-generated
  content disclosure in SMS messages

This library does **not** manage authentication or cryptography. Integrating
applications are responsible for securing Twilio/Pipecat/LiveKit credentials
and Redis access.

## Disclosure Policy

- Confirmation within 72 hours
- Initial assessment within 7 days
- Patch target within 30 days of confirmed vulnerability
- Credit in release notes unless anonymity preferred
