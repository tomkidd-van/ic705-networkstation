# License and provenance governance

## Publication posture

This snapshot is prepared for public publication under Apache-2.0.  The credential encoder and runtime lookup are independently derived project artifacts and are not described as inherited from any third-party client implementation.

## Credential handling

The Icom LAN credential encoder is justified by controlled IC-705 authentication/protocol experiments, redacted observation rows, generated lookup artifacts, packet-field notes and validation cases retained in this repository.

The accepted implementation standard is provenance-based:

1. Do not copy protected source expression from prior implementations
2. Derive credential behaviour from controlled IC-705 authentication/protocol experiments
3. Preserve the derivation method, generated lookup artifacts, packet-field notes and validation cases
4. Ensure the runtime encoder can be independently justified by the included artifacts

## Public framing

> The IC-705 LAN credential encoder and lookup data in this project were independently derived from controlled authentication experiments against the radio.  The derivation is documented through redacted observation rows, generated lookup artifacts, validation tools and packet-field experiment notes.  No third-party credential lookup table or client source implementation is included or required by the runtime.

## Apache public-release gate

For Apache-2.0 public distribution, complete all of the following as a release-management check:

- Confirm `LICENSE`, `NOTICE`, `pyproject.toml` and package classifiers all identify Apache-2.0 consistently
- Regenerate the credential lookup from the documented redacted observation set
- Run validation cases using controlled credentials
- Exclude all raw captures and station-specific logs from the public source tree
- Exclude station-specific local network addresses and private station metadata from public docs
- Confirm no old experimental files with pre comments are present
- Run a secret scan across the repository

## Runtime lookup data

The credential lookup is not embedded directly in the runtime source.  The runtime loads generated lookup data from the documented provenance artifact and package-local data copy:

- `docs/provenance/icom_lan_credential_encoding/generated_lookup.json`
- `icom_lan/data/credential_lookup.json`

This keeps the runtime transform tied to generated evidence artifacts that can be regenerated and validated.
