# Minimal credential coverage capture plan

This plan is the preferred fewest-test matrix for completing the Icom LAN credential lookup observations from controlled IC-705 authentication captures.

## Goal

The runtime credential encoder needs the printable lookup indexes `0x20..0x7e`.

Each successful login capture can provide 32 observations:

- 16 bytes from the login username field at `0x40:0x50`
- 16 bytes from the login password field at `0x50:0x60`

Since there are 95 printable lookup indexes, the theoretical minimum is three login captures:

```text
ceil(95 / 32) = 3
```

The three controlled username/password pairs below cover every shifted lookup index `0x20..0x7e` exactly once, with one extra filler slot.  They avoid literal space and backslash as entered credential characters.

## Three controlled credential tests

### CAP-PASS-MIN-001

```text
username: !~!~!~!~!~!~!~!~
password: ][][][][][][][][
```

Coverage:

```text
login_username: 0x21,0x20,0x23,0x22,0x25,0x24,0x27,0x26,0x29,0x28,0x2b,0x2a,0x2d,0x2c,0x2f,0x2e
login_password: 0x5d,0x5c,0x5f,0x5e,0x61,0x60,0x63,0x62,0x65,0x64,0x67,0x66,0x69,0x68,0x6b,0x6a
```

### CAP-PASS-MIN-002

```text
username: 0000000000000000
password: @@@@@@@@@@@@@@@@
```

Coverage:

```text
login_username: 0x30 through 0x3f
login_password: 0x40 through 0x4f
```

### CAP-PASS-MIN-003

```text
username: PPPPPPPPPPPP````
password: pppppppppppppppA
```

Coverage:

```text
login_username: 0x50 through 0x5b and 0x6c through 0x6f
login_password: 0x70 through 0x7e; final A is filler/duplicate coverage
```

## Capture procedure

For each test:

1. Save the current radio LAN credentials outside the repository
2. Configure the radio LAN username/password to the test pair
3. Start packet capture on the trusted LAN
4. Run a successful login/probe from this package
5. Extract the outbound login packet credential fields:
   - username field: `0x40:0x50`
   - password field: `0x50:0x60`
6. Add one observation batch for the username field and one for the password field
7. Restore original credentials after all captures are complete

## Observation builder commands

Replace `<USER_FIELD_16_BYTES>` and `<PASS_FIELD_16_BYTES>` with the observed 16-byte fields from the accepted login packet.

```bash
python3 tools/build_credential_observations.py \
  --case-id CAP-PASS-MIN-001 \
  --credential-kind login_username \
  --plaintext '!~!~!~!~!~!~!~!~' \
  --encoded-hex '<USER_FIELD_16_BYTES>' \
  --source controlled-capture \
  --notes 'minimal coverage plan test 1 username field 0x40:0x50' \
  --append docs/provenance/icom_lan_credential_encoding/observations_redacted.csv

python3 tools/build_credential_observations.py \
  --case-id CAP-PASS-MIN-001 \
  --credential-kind login_password \
  --plaintext '][][][][][][][][' \
  --encoded-hex '<PASS_FIELD_16_BYTES>' \
  --source controlled-capture \
  --notes 'minimal coverage plan test 1 password field 0x50:0x60' \
  --append docs/provenance/icom_lan_credential_encoding/observations_redacted.csv
```

```bash
python3 tools/build_credential_observations.py \
  --case-id CAP-PASS-MIN-002 \
  --credential-kind login_username \
  --plaintext '0000000000000000' \
  --encoded-hex '<USER_FIELD_16_BYTES>' \
  --source controlled-capture \
  --notes 'minimal coverage plan test 2 username field 0x40:0x50' \
  --append docs/provenance/icom_lan_credential_encoding/observations_redacted.csv

python3 tools/build_credential_observations.py \
  --case-id CAP-PASS-MIN-002 \
  --credential-kind login_password \
  --plaintext '@@@@@@@@@@@@@@@@' \
  --encoded-hex '<PASS_FIELD_16_BYTES>' \
  --source controlled-capture \
  --notes 'minimal coverage plan test 2 password field 0x50:0x60' \
  --append docs/provenance/icom_lan_credential_encoding/observations_redacted.csv
```

```bash
python3 tools/build_credential_observations.py \
  --case-id CAP-PASS-MIN-003 \
  --credential-kind login_username \
  --plaintext 'PPPPPPPPPPPP````' \
  --encoded-hex '<USER_FIELD_16_BYTES>' \
  --source controlled-capture \
  --notes 'minimal coverage plan test 3 username field 0x40:0x50' \
  --append docs/provenance/icom_lan_credential_encoding/observations_redacted.csv

python3 tools/build_credential_observations.py \
  --case-id CAP-PASS-MIN-003 \
  --credential-kind login_password \
  --plaintext 'pppppppppppppppA' \
  --encoded-hex '<PASS_FIELD_16_BYTES>' \
  --source controlled-capture \
  --notes 'minimal coverage plan test 3 password field 0x50:0x60' \
  --append docs/provenance/icom_lan_credential_encoding/observations_redacted.csv
```

## Official compile after capture

After the six observation-builder commands have been run, regenerate and validate:

```bash
python3 tools/derive_icom_lan_credential_lookup.py \
  --observations docs/provenance/icom_lan_credential_encoding/observations_redacted.csv \
  --output docs/provenance/icom_lan_credential_encoding/generated_lookup.json \
  --require-complete

cp docs/provenance/icom_lan_credential_encoding/generated_lookup.json \
  icom_lan/data/credential_lookup.json

python3 tools/validate_icom_lan_credential_lookup.py \
  --lookup docs/provenance/icom_lan_credential_encoding/generated_lookup.json \
  --cases docs/provenance/icom_lan_credential_encoding/validation_cases.csv \
  --require-complete

python3 -m compileall -q icom_lan tools
python3 tools/secret_scan.py .
```

## Notes

- The test credentials are controlled lab values only; do not reuse real radio credentials
- Do not commit unredacted packet captures
- Preserve only the observed field bytes, case id, radio model/firmware and accepted result
