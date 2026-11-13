# Regulatory source register

Reviewed 2026-11-13. These are implementation references, not legal advice or a
claim of comprehensive compliance. The code is MIT-licensed; linking a source
does not relicense its text or imply endorsement. Summaries here are project
interpretations, not copied regulatory text. Review laws, scope, exceptions and
implementation dates for each deployment.

| Area | Primary source | Implementation boundary |
|---|---|---|
| BAA | [HHS Business Associates guidance](https://www.hhs.gov/hipaa/for-professionals/privacy/guidance/business-associates/index.html) | Caller-attested agreement verification only; no contract validation. Not all disclosures require a BAA. |
| HIPAA restrictions / confidential communications | [HHS audit protocol, §164.522](https://www.hhs.gov/hipaa/for-professionals/compliance-enforcement/audit/protocol/index.html) | Legacy recording rule is a configurable safeguard, not this section's legal requirement. |
| ADMT requests | [CPPA adopted 2025 regulations](https://www.cppa.ca.gov/regulations/ccpa_updates), 11 CCR §7221; [CPPA implementation announcement](https://www.cppa.ca.gov/announcements/2025/20250923.html) | Request detection/routing, not applicability assessment or automatic external enforcement. CPPA identifies January 1, 2027 as the compliance start for significant-decision ADMT requirements. |
| Sensitive PI limits | [CPPA laws and regulations](https://cppa.ca.gov/regulations/), Civil Code §1798.121 | Distinct from ADMT; the legacy `CPRA-121` ID must not be treated as a valid statutory citation. This module does not implement this separate right. |
| FCC one-to-one rule | [FCC final rule, 2025-16641](https://www.federalregister.gov/d/2025-16641) | The challenged revised consent paragraph was vacated and removed. Per-campaign consent is a configurable safeguard, not enforcement of the vacated rule. Other consent requirements remain and need review. |
| EU human oversight | [European Commission Article 14](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-14) | Availability attestation is one signal, not full human oversight. Determine high-risk status separately using Article 6 / Annex III. |
| EU disclosure | [European Commission Article 50](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-50) | Caller-attested disclosure; applicability/exceptions are not inferred. |

Tests in `tests/test_compliance.py` cover missing and malformed evidence,
configuration opt-outs and advisory/strict modes. `tests/test_ccpa.py` covers
specific ADMT phrase precedence and the corrected citation. These tests prove
library behavior with synthetic inputs, not legal sufficiency or native deployment
qualification. The register covers corrections in this change, not every existing
TCPA, FERPA, HIPAA or CCPA mapping in the project.
