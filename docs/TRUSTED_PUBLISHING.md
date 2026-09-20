# PyPI trusted publishing

The existing test matrix and package job validate the release before a separate `publish` job uploads it. The package job checks source/runtime/tag identity, wheel and source-distribution metadata, strict Twine validation, isolated wheel imports and an uploaded-artifact round trip. The publish job downloads that same run's immutable artifact ID and uses GitHub OIDC to publish distributions and attestations. It does not check out, install or build application code. Only the publish job receives `id-token: write`; no publishing API-token fallback is configured.

## Account setup required before tagging a release

An authorized owner of the existing PyPI project must add this GitHub trusted publisher in its Publishing settings:

| Setting | Value |
|---|---|
| PyPI project | `voice-ai-governance` |
| GitHub owner | `ashutoshrana` |
| Repository | `voice-ai-governance` |
| Workflow filename | `ci.yml` |
| GitHub environment | `pypi` |

Create or verify the repository's matching `pypi` environment and its intended release-tag protections. Use the workflow filename `ci.yml`, not the full `.github/workflows/` path. Confirm the PyPI publisher and GitHub environment before pushing a new release tag. Workflow YAML alone does not establish account trust, and public package metadata cannot prove registration. Account setup remains unverified until an authorized owner checks it.

[Official PyPI registration instructions](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).

## Publication and verification

Publish only a new version whose `v` tag matches its source/runtime versions and whose intended commit passes all tests. The upload job depends on both `test` and `package`. The official pinned PyPA action uses short-lived OIDC credentials and has attestations explicitly enabled. If publisher identity is missing or mismatched, fix registration rather than restoring token upload or disabling attestations. Existing release files are not overwritten or retroactively attested. [PyPI attestation production guidance](https://docs.pypi.org/attestations/producing-attestations/).

In a separate verification environment:

```sh
python -m pip install pypi-attestations
```

For each released wheel and source archive, obtain its exact `files.pythonhosted.org` URL from the release's PyPI JSON metadata, assign it to `ARTIFACT_URL`, then run:

```sh
python -m pypi_attestations verify pypi \
  --repository https://github.com/ashutoshrana/voice-ai-governance \
  "$ARTIFACT_URL"
```

Require successful cryptographic verification for both artifacts and the expected repository. Inspect the verified signing identity/provenance against workflow `ci.yml`, environment `pypi`, and the intended tag/commit; preserve verifier output and artifact hashes with release records. An HTTP 200 provenance response or checksum alone is not signature verification. Provenance identifies publication; it does not certify application safety or privacy compliance. [Consumer verification guidance](https://docs.pypi.org/attestations/consuming-attestations/).

After an OIDC publication and independent verification succeed, an authorized owner may retire the obsolete publishing token if nothing else uses it. This workflow does not read or delete that secret; Codecov's separate coverage credential is unchanged.
