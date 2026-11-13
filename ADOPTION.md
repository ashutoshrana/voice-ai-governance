# Adoption

## PyPI Downloads

Historical download figures recorded from [pypistats.org](https://pypistats.org/packages/voice-ai-governance).

| Week of | Downloads |
|---------|-----------|
| 2026-04-20 | ~0 (new package) |

Download events do not establish unique users, independent adoption, or production installations. [PyPI Stats](https://pypistats.org/faqs) includes automated CI/CD downloads. Attributable usage evidence is needed before making adoption claims.

## How It Is Used

`voice-ai-governance` implements compliance enforcement middleware for voice
AI pipelines. It provides configurable controls intended to:

1. **Enforce TCPA quiet hours and opt-out interception** — automated calls
   and texts must comply with 47 U.S.C. § 227; consent state is tracked
   across sessions
2. **Scrub PHI/FERPA PII from post-call summaries** — HIPAA Minimum
   Necessary Rule (45 CFR § 164.502) and FERPA apply at the summary layer,
   not just during the call
3. **Preserve warm transfer state** — Redis-backed session continuity
   prevents context loss when handing off from AI to human agent
4. **Check AI-interaction disclosure** — [EU AI Act Article 50(1)](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-50) addresses informing people that they are interacting with AI, subject to its exceptions. The policy checks caller-supplied disclosure state; it does not itself deliver a disclosure or certify legal compliance.
5. **Gate escalation on ASR confidence** — low-confidence transcription
   triggers human escalation before the AI acts on misheard input

## Regulated Sector Coverage

| Sector | Regulations Addressed by Configurable Controls |
|--------|---------------------|
| Healthcare contact centers | HIPAA 45 CFR § 164, TCPA |
| Higher education enrollment | FERPA 34 CFR § 99, TCPA |
| Financial services outreach | GLBA, TCPA, CCPA |
| EU-facing deployments | EU AI Act Article 50(1) disclosure; Article 14 oversight where applicable |

## Related Packages

- [regulated-ai-governance](https://pypi.org/project/regulated-ai-governance/) — Policy enforcement for AI agent frameworks
- [enterprise-rag-patterns](https://pypi.org/project/enterprise-rag-patterns/) — FERPA/HIPAA/GDPR-compliant RAG retrieval patterns
- [confidence-escalation](https://pypi.org/project/confidence-escalation/) — Confidence-gated escalation middleware
