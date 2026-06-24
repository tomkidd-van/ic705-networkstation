#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build redacted credential-observation rows from controlled login cases.

This helper is for the independent IC-705 credential derivation workflow.  It
turns known test plaintext credentials and observed/captured 16-byte encoded
credential fields into per-character observation rows suitable for
``derive_icom_lan_credential_lookup.py``.

It intentionally does not contain a credential lookup table and does not encode
credentials.  It only records what was observed in a controlled authentication
experiment.

Example:
  python3 tools/build_credential_observations.py \
    --case-id cap-cred-001 \
    --credential-kind username \
    --plaintext ABC \
    --encoded-hex "47 4c 3e 00 00 00 00 00 00 00 00 00 00 00 00 00" \
    --append docs/provenance/icom_lan_credential_encoding/observations_redacted.csv
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

FIELDNAMES = [
    "case_id",
    "timestamp_utc",
    "radio_model",
    "firmware",
    "credential_kind",
    "plaintext_redacted",
    "plaintext_pattern",
    "position",
    "input_ord",
    "input_char",
    "encoded_byte",
    "packet_offset",
    "accepted",
    "response_code",
    "source",
    "notes",
]

BASE_OFFSETS = {
    "login_username": 0x40,
    "login_password": 0x50,
    "stream_username": 0x60,
}


def parse_hex_16(value: str) -> bytes:
    cleaned = value.replace(":", " ").replace(",", " ").replace("-", " ")
    parts = cleaned.split()
    if len(parts) == 1 and len(parts[0]) == 32:
        data = bytes.fromhex(parts[0])
    else:
        data = bytes(int(p, 16) for p in parts)
    if len(data) != 16:
        raise argparse.ArgumentTypeError(f"expected exactly 16 bytes, got {len(data)}")
    return data


def printable_pattern(text: str) -> str:
    out = []
    for ch in text:
        if ch.isalnum():
            out.append(ch)
        elif 32 <= ord(ch) <= 126:
            out.append(f"0x{ord(ch):02x}")
        else:
            out.append("?")
    return " ".join(out)


def append_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--timestamp-utc", default=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
    ap.add_argument("--radio-model", default="IC-705")
    ap.add_argument("--firmware", default="unknown")
    ap.add_argument("--credential-kind", choices=sorted(BASE_OFFSETS), required=True)
    ap.add_argument("--plaintext", required=True, help="Controlled test credential plaintext. Use test values only.")
    ap.add_argument("--encoded-hex", type=parse_hex_16, required=True, help="Observed 16-byte encoded credential field")
    ap.add_argument("--accepted", default="true")
    ap.add_argument("--response-code", default="0x00000000")
    ap.add_argument("--source", default="controlled-capture")
    ap.add_argument("--notes", default="")
    ap.add_argument("--append", type=Path, required=True)
    args = ap.parse_args()

    raw = args.plaintext.encode(errors="replace")[:16]
    encoded = args.encoded_hex
    base_offset = BASE_OFFSETS[args.credential_kind]
    rows: list[dict[str, str]] = []
    for position, byte_value in enumerate(raw):
        rows.append({
            "case_id": args.case_id,
            "timestamp_utc": args.timestamp_utc,
            "radio_model": args.radio_model,
            "firmware": args.firmware,
            "credential_kind": args.credential_kind,
            "plaintext_redacted": "controlled-test-value",
            "plaintext_pattern": printable_pattern(args.plaintext),
            "position": str(position),
            "input_ord": str(byte_value),
            "input_char": chr(byte_value) if 32 <= byte_value <= 126 else "",
            "encoded_byte": f"0x{encoded[position]:02x}",
            "packet_offset": f"0x{base_offset + position:02x}",
            "accepted": args.accepted,
            "response_code": args.response_code,
            "source": args.source,
            "notes": args.notes,
        })

    append_rows(args.append, rows)
    print(f"appended {len(rows)} observation row(s) to {args.append}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
