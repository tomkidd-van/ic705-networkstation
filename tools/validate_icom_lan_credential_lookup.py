#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Validate generated Icom LAN credential lookup artifact and local encoder output."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

PRINTABLE_MIN = 32
PRINTABLE_MAX = 126


def passcode_index(byte_value: int, position: int) -> int:
    idx = byte_value + position
    if idx > PRINTABLE_MAX:
        idx = PRINTABLE_MIN + (idx % 127)
    return idx


def encode(text: str, table: bytes) -> bytes:
    raw = text.encode(errors="replace")[:16]
    return bytes(table[passcode_index(b, i)] for i, b in enumerate(raw))


def parse_hex(value: str) -> bytes:
    cleaned = value.replace(":", " ").replace(",", " ").replace("-", " ")
    parts = cleaned.split()
    if len(parts) == 1:
        return bytes.fromhex(parts[0])
    return bytes(int(p, 16) for p in parts)


def load_table(path: Path, *, require_complete: bool = False) -> bytes:
    data = json.loads(path.read_text(encoding="utf-8"))
    values = data.get("sequence", data.get("lookup_decimal"))
    if not isinstance(values, list) or len(values) < 127:
        raise SystemExit(f"invalid lookup sequence in {path}")
    table = bytes(int(v) & 0xFF for v in values)
    if require_complete:
        missing = data.get("missing_printable_indexes")
        if missing:
            raise SystemExit(f"lookup artifact reports missing printable indexes: {missing}")
    return table


def validate_cases(path: Path, table: bytes) -> int:
    checked = 0
    failures: list[str] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for line_no, row in enumerate(reader, start=2):
            case_id = (row.get("case_id") or "").strip()
            if not case_id or case_id.lower() == "placeholder":
                continue
            for field_name in ("username", "password"):
                plaintext = row.get(field_name, "")
                expected_hex = row.get(f"expected_{field_name}_encoded_hex", "")
                if not expected_hex:
                    continue
                got = encode(plaintext, table)
                expected = parse_hex(expected_hex)
                checked += 1
                if got != expected:
                    failures.append(
                        f"{path}:{line_no}:{field_name}: expected {expected.hex(' ')}, got {got.hex(' ')}"
                    )
    if failures:
        raise SystemExit("validation case failures:\n" + "\n".join(failures))
    return checked


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lookup", type=Path, default=Path("docs/provenance/icom_lan_credential_encoding/generated_lookup.json"))
    ap.add_argument("--encode", dest="texts", action="append", default=[], help="Plaintext credential to encode locally")
    ap.add_argument("--cases", type=Path, default=None, help="Optional validation_cases.csv with expected encoded fields")
    ap.add_argument("--require-complete", action="store_true")
    args = ap.parse_args()

    table = load_table(args.lookup, require_complete=args.require_complete)
    print(f"lookup ok: {args.lookup} ({len(table)} bytes)")
    for text in args.texts:
        print(f"{text!r}: {encode(text, table).hex(' ')}")
    if args.cases:
        checked = validate_cases(args.cases, table)
        print(f"validation cases checked: {checked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
