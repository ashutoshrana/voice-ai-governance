
## Common Failure Patterns

| Symptom | Root cause | Fix |
|---|---|---|
| Handoff leaks and lost updates | Original entities in summaries and nontransactional Redis | Scrub complete payload and use WATCH/MULTI with real Redis regression tests |

| Redis tests pass on macOS but fail on Linux CI | Test fixture hardcoded a macOS-only temporary directory | Use the platform temporary directory; verify local Redis tests and Linux CI |
| Package and runtime versions diverge or reuse an existing release | Release metadata was not validated against runtime, tag, and artifacts | Synchronize versions, verify wheel/sdist metadata and isolated imports, publish only the validated artifact |
| A closed session can be reopened through a retrieved object; PII survives in keys | Local storage shared object references and scrubber visited values only | Copy state at read/write boundaries and redact keys with collision rejection |
| Custom key patterns expose sensitive values | Sensitive-key classification used the already-redacted label | Classify the original key before changing its output representation; exercise a custom label pattern |

| Context publication disconnects a caller despite delivery failure | Adapter broadcast to participants, suppressed SDK errors, and treated packet submission as completed transfer | Require one selected recipient and valid session; propagate failures; leave connection, acknowledgment, and terminal state to native orchestration; the handoff contract negative control rejects packet-as-completion regressions |
