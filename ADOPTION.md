# Adoption

## PyPI Downloads

Verified via [pypistats.org](https://pypistats.org/packages/voice-ai-governance).

| Week of | Downloads |
|---------|-----------|
| 2026-04-20 | ~0 (new package) |

Downloads are organic — no self-installs, no promotional campaigns.
Weekly downloads tracked from PyPI release date.

## How It Is Used

`voice-ai-governance` implements compliance enforcement middleware for voice
AI pipelines. Teams use it to:

1. **Enforce TCPA quiet hours and opt-out interception** — automated calls
   and texts must comply with 47 U.S.C. § 227; consent state is tracked
   across sessions
2. **Scrub PHI/FERPA PII from post-call summaries** — HIPAA Minimum
   Necessary Rule (45 CFR § 164.502) and FERPA apply at the summary layer,
   not just during the call
3. **Preserve warm transfer state** — Redis-backed session continuity
   prevents context loss when handing off from AI to human agent
4. **Enforce EU AI Act AI disclosure** — Article 13 requires users to
   know they are interacting with an AI; enforced at session open
5. **Gate escalation on ASR confidence** — low-confidence transcription
   triggers human escalation before the AI acts on misheard input

## Regulated Sector Coverage

| Sector | Regulations Enforced |
|--------|---------------------|
| Healthcare contact centers | HIPAA 45 CFR § 164, TCPA |
| Higher education enrollment | FERPA 34 CFR § 99, TCPA |
| Financial services outreach | GLBA, TCPA, CCPA |
| EU-facing deployments | EU AI Act Articles 13, 14 |

## Related Packages

- [regulated-ai-governance](https://pypi.org/project/regulated-ai-governance/) — Policy enforcement for AI agent frameworks
- [enterprise-rag-patterns](https://pypi.org/project/enterprise-rag-patterns/) — FERPA/HIPAA/GDPR-compliant RAG retrieval patterns
- [confidence-escalation](https://pypi.org/project/confidence-escalation/) — Confidence-gated escalation middleware
