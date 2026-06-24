# Session management provenance summary

This package retains only the minimum session-management evidence needed for publication review.  It does not include raw captures or station-specific network data.

## Publicly retained facts

- The login payload contains separate 16-byte encoded username and password fields
- The same generated credential encoder is used for both fields
- Accepted controlled authentication observations use response code `0x00000000`
- Login/session state is established before control, CI-V and stream operations proceed
- Keepalive, retry/relogin and shutdown handling are implemented in the station/session runtime rather than in ad hoc scripts
- TX-facing station behaviour gates audio on confirmed PTT and waits for PTT-off readback during shutdown/recovery paths

## Implementation locations

| Area | Runtime files |
|---|---|
| Credential loading/encoding | `icom_lan/auth.py`, `icom_lan/data/credential_lookup.json` |
| Control packet/session protocol | `icom_lan/protocol/control.py`, `icom_lan/protocol/packets.py`, `icom_lan/protocol/udp.py` |
| Session lifecycle and retry/relogin | `icom_lan/session/lifecycle.py` |
| Station orchestration and shutdown | `icom_lan/station/runner.py`, `icom_lan/station/state.py`, `icom_lan/station/keepalive.py` |
| CAT/PTT handling | `icom_lan/civ/cat.py`, `icom_lan/civ/ptt.py`, `icom_lan/rigctl/handlers.py` |

## Evidence boundary

The public repository demonstrates the credential transform through machine-checkable redacted observations and validation cases.  Session management is documented as a concise behavioural summary tied to runtime source locations.  Raw session captures, local IPs, operator notes and station logs remain excluded.
