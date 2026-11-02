
## Common Failure Patterns

| Symptom | Root cause | Fix |
|---|---|---|
| Handoff leaks and lost updates | Original entities in summaries and nontransactional Redis | Scrub complete payload and use WATCH/MULTI with real Redis regression tests |

| Redis tests pass on macOS but fail on Linux CI | Test fixture hardcoded a macOS-only temporary directory | Use the platform temporary directory; verify local Redis tests and Linux CI |
| Package and runtime versions diverge or reuse an existing release | Release metadata was not validated against runtime, tag, and artifacts | Synchronize versions, verify wheel/sdist metadata and isolated imports, publish only the validated artifact |
| A closed session can be reopened through a retrieved object; PII survives in keys | Local storage shared object references and scrubber visited values only | Copy state at read/write boundaries and redact keys with collision rejection |
| Custom key patterns expose sensitive values | Sensitive-key classification used the already-redacted label | Classify the original key before changing its output representation; exercise a custom label pattern |

| Context publication disconnects a caller despite delivery failure | Adapter broadcast to participants, suppressed SDK errors, and treated packet submission as completed transfer | Require one selected recipient and valid session; propagate failures; leave connection, acknowledgment, and terminal state to native orchestration; the handoff contract negative control rejects packet-as-completion regressions |

| Requested PII redaction silently returns an unsanitized handoff | Shared payload construction treated an absent scrubber as permission to bypass requested redaction | Reject requested redaction without a configured scrubber in the shared sync/async builder; preserve missing-session None and require an explicit opt-out for raw payloads |
| Release workflow uses an advisory-affected or mutable publishing action | A dependency update in one repository left equivalent release workflows unchanged | Pin every publishing workflow to the verified patched action commit and validate each workflow before release |
| Python 3.9 CI fails before tests start | ubuntu-latest moved to Ubuntu 26.04 without Python 3.9 builds | Pin the supported Python test matrix to Ubuntu 24.04 and verify the official Python version manifest |

| The first escalation quick start fails at import in a separate project | The existing threshold class was omitted from the public package exports while the README imported it there | Export the existing class at the package root and module __all__; execute the documented example in regression coverage |

| Python 3.9 development installs select an advisory-affected pytest release | Patched pytest requires Python 3.10 while the dependency-free library supports Python 3.9 | Require pytest>=9.0.3 for development, test fully on Python 3.10–3.12, and separately compile and exercise an isolated Python 3.9 base wheel without development dependencies |
| Release artifact checks do not prove the downloaded bytes match the tested package | Separate upload/download steps and implicit checkout selection leave source and artifact identity unverified | Check out the event SHA without persisted credentials, validate wheel and sdist installs, retain SHA256SUMS with the run artifact, and verify hashes after round trip and before publication |
| Release roundtrip accepts substituted files with matching version metadata | Identity checks omit exact distribution bytes | Verify the retained artifact checksum manifest after download and again before OIDC publication |

| README describes an available release as unpublished or labels an older release current | A release-time version snapshot was kept as permanent project status prose | Link to the stable PyPI project and changelog instead of embedding current-release assertions; preserve historical published artifact text |
| A legacy optional installer cannot resolve a patched dependency graph | Older SDK constraints cap affected dependencies below their patched release | Remove the unqualified installer and aggregate entry, retain caller-owned helper APIs, and document the native migration boundary |
| Redis minimum qualification fails during test teardown | Redis 4.x exposes async close while current Redis exposes aclose | Close the fixture through the available API and run the real Redis suite on both qualified graphs |
