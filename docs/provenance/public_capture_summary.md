# Public capture summary

This file is the publication-safe substitute for the internal packet-capture archive.  It records only the distilled protocol facts needed by the public documentation and deliberately excludes raw frames, packet-capture files, station logs, hostnames, private LAN addresses, MAC-like identifiers and real operator credentials.

## Retained packet-field facts

- Login packets contain fixed-width credential fields for username and password
- The username field occupies the 16-byte field documented by the credential capture plan as `0x40:0x50`
- The password field occupies the 16-byte field documented by the credential capture plan as `0x50:0x60`
- Accepted controlled authentication observations use response code `0x00000000`
- The same generated credential lookup is used for both username and password field encoding
- Login/session establishment precedes control, CI-V and stream operations

## Retained public artifacts

- `docs/provenance/icom_lan_credential_encoding/observations_redacted.csv` contains one redacted row per accepted credential lookup observation
- `docs/provenance/icom_lan_credential_encoding/generated_lookup.json` is regenerated from the redacted observation rows
- `docs/provenance/icom_lan_credential_encoding/validation_cases.csv` contains controlled test credentials and expected encoded bytes
- `docs/provenance/icom_lan_credential_encoding/session_management.md` summarizes session/runtime behaviour without publishing raw capture data

## Evidence boundary

The internal provenance archive may retain raw captures for private audit.  The public release should retain only reduced packet-field facts, controlled test credentials, generated lookup artifacts and validation tooling.  Future capture-derived documentation should be added here or to the credential provenance directory only after redaction to this same level.
