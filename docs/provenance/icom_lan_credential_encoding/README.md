# Credential provenance

This directory keeps the minimum evidence needed to audit the IC-705 LAN credential encoder without publishing raw packet captures, station logs, private IP addresses or real operator credentials.

## Retained evidence

| File | Purpose |
|---|---|
| `observations_redacted.csv` | One row per accepted controlled observation needed to cover printable credential lookup indexes `0x20..0x7e` |
| `generated_lookup.json` | Lookup artifact regenerated from the observation CSV and mirrored into `icom_lan/data/credential_lookup.json` for runtime use |
| `validation_cases.csv` | Controlled username/password test cases that confirm representative local encodes |
| `session_management.md` | Short publication-safe summary of login/session behaviour and where it is implemented |

The observation rows use controlled test credentials only.  They retain the plaintext character, zero-based position, observed encoded byte, packet-field offset and accepted result code needed to regenerate the lookup table.  They do not contain real station credentials.

## Credential rule

For each username or password character:

1. Add the zero-based character position to the input byte
2. If the result is greater than `0x7e`, wrap into printable ASCII with `0x20 + (value % 0x7f)`
3. Use that value as the index into the generated lookup table

Username and password fields use the same encoder.  The runtime loads the generated table rather than embedding it as source code.

## Reproduce the artifact

```bash
python3 tools/derive_icom_lan_credential_lookup.py \
  --observations docs/provenance/icom_lan_credential_encoding/observations_redacted.csv \
  --output /tmp/generated_lookup.json \
  --require-complete

python3 tools/validate_icom_lan_credential_lookup.py \
  --lookup docs/provenance/icom_lan_credential_encoding/generated_lookup.json \
  --cases docs/provenance/icom_lan_credential_encoding/validation_cases.csv \
  --require-complete
```

For review, compare the generated `/tmp/generated_lookup.json` `sequence` to both checked-in copies:

- `docs/provenance/icom_lan_credential_encoding/generated_lookup.json`
- `icom_lan/data/credential_lookup.json`

## Excluded from the public tree

Raw `.pcap`/`.pcapng` captures, capture ZIPs, logs, hostnames, private LAN addresses, MAC-like identifiers and real credentials belong only in the internal evidence archive.  Future public evidence should be reduced to packet-field rows or short summaries before publication.
