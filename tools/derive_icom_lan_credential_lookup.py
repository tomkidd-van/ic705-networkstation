#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate Icom LAN credential lookup data from controlled observations.

The input is a redacted CSV containing one row per observed credential
character.  The tool intentionally contains no credential lookup table and does
not consult any prior implementation.  It converts accepted radio/protocol
observations into the generated lookup artifact consumed by the runtime.

Accepted input column names, with aliases for earlier package versions:
  position,input_ord,encoded_byte,accepted

Additional provenance columns are preserved in summary form when present:
  case_id,timestamp_utc,radio_model,firmware,credential_kind,packet_offset,
  response_code,source,notes
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PRINTABLE_MIN = 32
PRINTABLE_MAX = 126
LOOKUP_LENGTH = 160
REQUIRED_COLUMNS = {"position", "input_ord", "encoded_byte"}


def passcode_index(byte_value: int, position: int) -> int:
    idx = byte_value + position
    if idx > PRINTABLE_MAX:
        idx = PRINTABLE_MIN + (idx % 127)
    return idx


def parse_int(value: str | None) -> int:
    if value is None:
        raise ValueError("missing integer value")
    value = str(value).strip()
    if value == "":
        raise ValueError("empty integer value")
    if value.lower().startswith("0x"):
        return int(value, 16)
    return int(value)


def truthy(value: str | None) -> bool:
    if value is None or str(value).strip() == "":
        return True
    return str(value).strip().lower() in {"1", "true", "yes", "y", "accepted", "ok", "success"}


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def row_case_id(row: dict[str, str]) -> str:
    return (row.get("case_id") or row.get("case") or "").strip()



def dumps_lookup_artifact(artifact: dict[str, Any]) -> str:
    """Return stable, readable JSON with compact scalar arrays.

    The lookup artifact contains several long scalar arrays.  Python's default
    ``json.dumps(..., indent=2)`` emits one value per line, which makes the
    checked-in provenance artifact noisy.  This formatter keeps top-level fields
    readable while grouping long arrays into short chunks.
    """

    def scalar_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))

    def format_array(values: list[Any], indent: int) -> str:
        if not values:
            return "[]"
        item_text = [scalar_json(v) for v in values]
        if len(item_text) <= 12:
            return "[" + ", ".join(item_text) + "]"
        chunk_size = 16
        child = " " * (indent + 2)
        parent = " " * indent
        lines = ["["]
        for start in range(0, len(item_text), chunk_size):
            chunk = ", ".join(item_text[start : start + chunk_size])
            if start + chunk_size < len(item_text):
                chunk += ","
            lines.append(child + chunk)
        lines.append(parent + "]")
        return "\n".join(lines)

    def format_value(value: Any, indent: int) -> str:
        if isinstance(value, list):
            return format_array(value, indent)
        if isinstance(value, dict):
            return format_object(value, indent)
        return scalar_json(value)

    def format_object(obj: dict[str, Any], indent: int = 0) -> str:
        parent = " " * indent
        child = " " * (indent + 2)
        lines = ["{"]
        items = list(obj.items())
        for idx, (key, value) in enumerate(items):
            suffix = "," if idx < len(items) - 1 else ""
            formatted = format_value(value, indent + 2)
            lines.append(f"{child}{scalar_json(key)}: {formatted}{suffix}")
        lines.append(parent + "}")
        return "\n".join(lines)

    return format_object(artifact)

def derive(input_csv: Path, output_json: Path, *, require_complete: bool) -> None:
    lookup: list[int | None] = [None] * LOOKUP_LENGTH
    conflicts: list[str] = []
    accepted_rows = 0
    skipped_rows = 0
    case_ids: set[str] = set()
    radio_models: set[str] = set()
    firmware_versions: set[str] = set()
    credential_kinds: set[str] = set()
    sources: set[str] = set()

    with input_csv.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise SystemExit(f"{input_csv}: empty CSV or missing header")
        missing_columns = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing_columns:
            raise SystemExit(f"{input_csv}: missing required column(s): {', '.join(sorted(missing_columns))}")

        for line_no, row in enumerate(reader, start=2):
            # Skip placeholder rows from package scaffolding.
            if (row_case_id(row).lower() == "placeholder") or str(row.get("notes", "")).startswith("Redacted raw observations"):
                skipped_rows += 1
                continue
            if not truthy(row.get("accepted")):
                skipped_rows += 1
                continue
            try:
                position = parse_int(row.get("position"))
                input_ord = parse_int(row.get("input_ord"))
                encoded_byte = parse_int(row.get("encoded_byte"))
            except Exception as exc:
                raise SystemExit(f"{input_csv}:{line_no}: invalid observation row: {exc}") from exc

            if not 0 <= position <= 15:
                raise SystemExit(f"{input_csv}:{line_no}: position out of range: {position}")
            if not 0 <= input_ord <= 255:
                raise SystemExit(f"{input_csv}:{line_no}: input_ord out of byte range: {input_ord}")
            if not 0 <= encoded_byte <= 255:
                raise SystemExit(f"{input_csv}:{line_no}: encoded_byte out of byte range: {encoded_byte}")

            idx = passcode_index(input_ord, position)
            existing = lookup[idx]
            if existing is not None and existing != encoded_byte:
                conflicts.append(
                    f"index {idx}: existing 0x{existing:02x}, new 0x{encoded_byte:02x} at line {line_no}"
                )
            lookup[idx] = encoded_byte & 0xFF
            accepted_rows += 1

            for col, target in (
                ("case_id", case_ids),
                ("radio_model", radio_models),
                ("firmware", firmware_versions),
                ("credential_kind", credential_kinds),
                ("source", sources),
            ):
                val = str(row.get(col, "")).strip()
                if val:
                    target.add(val)

    if conflicts:
        raise SystemExit("Conflicting observations:\n" + "\n".join(conflicts))

    missing = [i for i in range(PRINTABLE_MIN, PRINTABLE_MAX + 1) if lookup[i] is None]
    if require_complete and missing:
        raise SystemExit(f"lookup is incomplete; missing printable indexes: {missing}")

    values = [0 if v is None else int(v) for v in lookup]
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    artifact: dict[str, Any] = {
        "schema": "icom_lan_credential_lookup.v2",
        "status": "generated-from-controlled-ic705-observations" if not missing else "incomplete-generated-from-controlled-observations",
        "generated_at_utc": generated_at,
        "generated_by": "tools/derive_icom_lan_credential_lookup.py",
        "source_observations": str(input_csv),
        "source_observations_sha256": file_sha256(input_csv),
        "accepted_observation_rows": accepted_rows,
        "skipped_rows": skipped_rows,
        "unique_case_ids": sorted(case_ids),
        "radio_models": sorted(radio_models),
        "firmware_versions": sorted(firmware_versions),
        "credential_kinds": sorted(credential_kinds),
        "observation_sources": sorted(sources),
        "credential_length": 16,
        "index_rule": "index = input_byte + zero_based_position; if index > 126, index = 32 + (index % 127)",
        "lookup_length": len(values),
        "lookup_decimal": values,
        "sequence": values,
        "lookup_hex": [f"0x{x:02x}" for x in values],
        "missing_printable_indexes": missing,
        "complete_printable_ascii": not missing,
        "provenance_note": (
            "Generated from controlled IC-705 authentication observations. This artifact is intended to be "
            "reproducible from the redacted observation CSV and does not require consulting prior client source."
        ),
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(dumps_lookup_artifact(artifact) + "\n", encoding="utf-8")
    print(f"wrote {output_json}")
    print(f"accepted rows: {accepted_rows}")
    print(f"skipped rows: {skipped_rows}")
    if missing:
        print(f"missing printable indexes: {len(missing)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--observations", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--require-complete", action="store_true", help="Fail unless every printable index 0x20-0x7e is observed")
    args = ap.parse_args()
    derive(args.observations, args.output, require_complete=args.require_complete)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
