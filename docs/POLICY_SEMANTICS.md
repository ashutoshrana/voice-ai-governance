# Policy decisions and migration

These policies inspect caller-supplied facts. They do not inspect contracts,
certify compliance, classify legal applicability, operate telephony, or perform
all required safeguards. Applications must apply decisions before the relevant
action and retain appropriate evidence. See [sources](REGULATORY_SOURCES.md).

## Changes after 0.5.1

- `HIPAAVoicePolicy(require_baa_verification=True)` now requires literal
  `baa_verified=True` on every check. Previously the flag was unused. Missing,
  false, strings and numeric values fail with `HIPAA-164.502-BAA-01` and
  `verify_baa`. Supply this attestation only from a trusted contract-review
  process, never a caller transcript. Disable the requirement explicitly only
  when an authorized deployment review establishes that this check is inapplicable
  or managed outside this policy. A BAA is not required for every HIPAA disclosure.
- When high-risk use and human override are configured, EU checks require literal
  `human_override_available=True`. Missing data formerly passed. Verify actual
  availability; a transfer function alone does not establish Article 14 compliance.
- The `scrub_phi_before_transfer` option now controls its named-field check.
  Turning it off disables that check; it does not scrub or authorize raw disclosure.
- `block_on_violation=True` (keyword-only, HIPAA/EU) also blocks `VIOLATION`
  severity. Default `False` preserves advisory behavior for recording consent
  and AI disclosure: `passed=True` can coexist with violations and required
  actions. `CRITICAL` always fails. Select strict mode when using `passed` as a
  gate; handle all required actions regardless of mode. FERPA retains its existing
  critical-only semantics; CCPA returns `passed=False` for every detected request
  to request routing, not to declare a proven statutory violation.

## Adapter boundary

LiveKit and Amazon Connect pass `state.platform_metadata['baa_verified']` into
HIPAA checks, without inventing a positive default. Set it from a trusted
application configuration/contract-review process, not untrusted job metadata.
LiveKit's default HIPAA policy requires this evidence; Amazon Connect's default
explicitly disables the BAA check (external review). An injected policy can enable
it. Payload construction returns audit decisions; callers must not mistake a
payload or successful publication for authorization or proof of legal compliance.
Inspect LiveKit’s `hipaa_audit` and Amazon Connect’s
`attributes["HIPAAViolations"]` before acting.

**Publication now gates on the policy decision:** LiveKit `transfer()` raises
`RuntimeError` before publishing when `hipaa_audit.passed` is absent or not true.
`on_confidence_low()` checks before recording escalation state, then rechecks at
publication. A failed check does not publish or disconnect the caller. If state
changes between these checks, the second can reject publication after escalation
metadata was recorded; this is not a transactional lock on external evidence. Previously
these methods ignored a failing result. Applications using the default policy
must supply the trusted BAA attestation or explicitly configure a policy that
manages this check elsewhere. Advisory noncritical violations still follow the
selected `block_on_violation` mode. This is not proof of legal compliance, human
acceptance or successful transfer. Payload-building alone remains audit-only.

## Compatibility identifiers and legal scope

- `HIPAA-164.522-01` remains the recording-safeguard identifier. It is NOT a citation
  that HIPAA §164.522 requires recording consent. That section covers restrictions
  and confidential communications. Configure recording requirements for the actual
  jurisdiction and use case; the policy does not select the governing law.
- `EUAIA-13.1-01` remains the AI disclosure identifier; the reference is Article
  50(1), with exceptions. `EUAIA-14.3-01` remains an oversight-check identifier,
  not a representation that one boolean implements all of Article 14.
- `CPRA-121` remains the ADMT request identifier. The opt-out source is 11 CCR
  §7221, not Civil Code §1798.121 (sensitive personal information limits). This
  module detects a request and recommends pausing/routing for review; it does not
  enforce deadlines, delete records, stop an external decision engine or assess
  covered businesses, significant decisions, exceptions or effective dates.
  The adopted ADMT requirements have a January 1, 2027 compliance start for
  businesses using ADMT to make significant decisions. This conservative request
  handler is usable before then; it does not call every earlier request a breach.

Pattern scrubbing is not HIPAA Safe Harbor or Expert Determination. Unrecognized
names, clinical facts and other sensitive material can remain. SMS builders do
not secure portal links, mint access tokens, authorize recipients or prove consent.
