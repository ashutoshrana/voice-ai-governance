## Summary
<!-- What does this PR add or fix? -->

## Motivation
<!-- What compliance gap or telephony use case does this address? -->

## Changes
- [ ] New telephony platform adapter
- [ ] New compliance policy (TCPA / HIPAA / EU AI Act)
- [ ] Bug fix
- [ ] Documentation
- [ ] Tests

## Regulatory context
<!-- Which regulations does this implement? TCPA § 227? HIPAA 45 CFR 164? EU AI Act Art. 14? -->

## Tests
<!-- Describe the tests added or updated. No real PII in test data. -->

## Checklist
- [ ] Tests pass (`pytest tests/ --no-header`)
- [ ] Lint passes (`ruff check src/ tests/`)
- [ ] TCPA consent checked before any AI-initiated voice contact
- [ ] PHI scrubber runs on all transcripts before LLM processing
- [ ] EU AI Act Art. 13 disclosure fires at session start
- [ ] Audit log produced for every call decision
- [ ] No real patient data, PII, or telephony credentials in tests or examples
- [ ] Patterns work with any telephony provider (adapters live in `adapters/`, not core)
