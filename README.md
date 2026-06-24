# IC-705 Icom LAN Station Client

Experimental Python station client for Icom LAN-capable radios, currently focused on the IC-705.  It can establish an Icom LAN session, negotiate audio/CI-V streams, expose a local rigctl-compatible facade and bridge RX/TX PCM audio for workflows such as WSJT-X, fldigi experiments and Direwolf/APRS testing.

This project is independent software.  It is not affiliated with, endorsed by or supported by Icom Inc.

## Current status

This repository snapshot is prepared as a public publication target under **Apache-2.0**.  The runtime uses credential lookup data independently derived from controlled IC-705 authentication experiments, redacted observation records, packet-field notes and generated validation artifacts.

Functional state represented by this v103 multi-file snapshot:

- IC-705 LAN discovery, login, token handling and stream allocation
- RX audio capture/playback and file/FIFO/stdout sinks
- TX audio paths gated by radio-confirmed PTT
- Local rigctl-compatible facade for selected CAT/PTT workflows
- Conservative stream defaults plus an RX-only-minimal profile for RX/probe commands
- No embedded radio host, username or password

## Requirements

- Python 3.10+
- Optional audio support:

```bash
python3 -m pip install sounddevice numpy
```

## Basic use

Set radio connection settings through environment variables or CLI flags:

```bash
export ICOM_HOST=<radio-ip>
export ICOM_USER=<radio-user>
export ICOM_PASSWORD='<radio-password>'
```

Probe the radio:

```bash
python3 -m icom_lan -v probe
```

Record RX audio:

```bash
python3 -m icom_lan -v rx-record --seconds 10 --prefix rx_test
```

Run the local station/rigctl facade:

```bash
python3 -m icom_lan -v station --station-rx-audio --station-tx-audio --rigctld-debug-bytes
```

List local audio devices:

```bash
python3 -m icom_lan list-audio-devices
```

## Safety notes

- Do not expose Icom LAN control or this local rigctl facade directly to untrusted networks
- Prefer VPN, SSH tunnel or a physically trusted LAN segment for remote operation
- RX/probe commands avoid CI-V/PTT/TX operations
- TX audio is gated by radio-confirmed PTT in station mode
- Use low RF power and a dummy load or appropriate antenna/test setup when validating TX paths

## Credential encoder provenance

The credential encoder is the primary provenance item in this project.  See:

- `docs/governance/license_provenance.md`
- `docs/provenance/icom_lan_credential_encoding/README.md`
- `docs/provenance/icom_lan_credential_encoding/minimal_capture_plan.md`
- `docs/provenance/icom_lan_credential_encoding/session_management.md`

The project uses a provenance-based implementation standard: derive the credential lookup from controlled IC-705 authentication experiments, preserve the derivation method, generated lookup data and validation artifacts and does not rely on prior implementation source as the basis for the runtime encoder.

### Independent derivation workflow

The repository includes derivation tooling for the credential encoder:

```bash
python3 tools/build_credential_observations.py --help
python3 tools/derive_icom_lan_credential_lookup.py --help
python3 tools/validate_icom_lan_credential_lookup.py --help
python3 tools/icom_lan_auth_oracle.py --help
```

The workflow is documented in `docs/provenance/icom_lan_credential_encoding/README.md` and `docs/provenance/icom_lan_credential_encoding/minimal_capture_plan.md`.  These tools are for controlled experiments against your own radio and do not change the normal station runtime.

## Public evidence policy

This Apache publication target intentionally excludes raw packet captures, packet-capture archives, station logs, local network addresses and private station metadata.  The public provenance set contains distilled packet-field notes, redacted observation rows, controlled test credentials and validation artifacts sufficient to document the credential encoder and runtime behaviour without publishing raw lab traffic.

## License

This publication snapshot is licensed under Apache-2.0.  See `LICENSE` and `NOTICE`.

## Credential lookup artifact

The runtime loads the Icom LAN credential lookup from `docs/provenance/icom_lan_credential_encoding/generated_lookup.json` when running from source.  A package-local copy is also included under `icom_lan/data/credential_lookup.json` so editable/installable package runs keep the same behaviour.

## Credential derivation status

The credential lookup used by the runtime is generated from the checked-in redacted observation ledger under `docs/provenance/icom_lan_credential_encoding/`.  The official compile pass regenerates `generated_lookup.json` with `--require-complete`, synchronizes it to `icom_lan/data/credential_lookup.json` and validates representative controlled credentials.  The repository uses a provenance-based, independently justified implementation strategy.
