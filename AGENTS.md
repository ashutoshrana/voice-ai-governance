
## Common Failure Patterns

| Symptom | Root cause | Fix |
|---|---|---|
| Handoff leaks and lost updates | Original entities in summaries and nontransactional Redis | Scrub complete payload and use WATCH/MULTI with real Redis regression tests |

| Redis tests pass on macOS but fail on Linux CI | Test fixture hardcoded a macOS-only temporary directory | Use the platform temporary directory; verify local Redis tests and Linux CI |
