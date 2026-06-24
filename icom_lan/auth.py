from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


def _lookup_paths() -> tuple[Path, ...]:
    repo_root = Path(__file__).resolve().parents[1]
    return (
        repo_root / "docs" / "provenance" / "icom_lan_credential_encoding" / "generated_lookup.json",
        Path(__file__).resolve().parent / "data" / "credential_lookup.json",
    )


@lru_cache(maxsize=1)
def credential_lookup_sequence() -> bytes:
    last_error: Exception | None = None
    for path in _lookup_paths():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            seq = data.get("sequence", data.get("lookup_decimal"))
            if not isinstance(seq, list) or len(seq) < 127:
                raise RuntimeError(f"invalid credential lookup sequence length in {path}")
            return bytes(int(x) & 0xFF for x in seq)
        except FileNotFoundError as exc:
            last_error = exc
            continue
        except (TypeError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
            raise RuntimeError(f"Invalid credential lookup data in {path}: {exc}") from exc

    searched = ", ".join(str(path) for path in _lookup_paths())
    raise RuntimeError(
        "Missing Icom LAN credential lookup data. "
        f"Searched: {searched}. See docs/provenance/icom_lan_credential_encoding/."
    ) from last_error


def passcode_index(byte_value: int, position: int) -> int:
    """Return the shifted printable-ASCII lookup index for one credential byte.

    The runtime lookup data is generated from controlled IC-705 credential
    authentication observations. See docs/provenance/icom_lan_credential_encoding/.
    """
    p = byte_value + position
    if p > 126:
        p = 32 + (p % 127)
    return p


def passcode(text: str) -> bytes:
    raw = text.encode(errors="replace")[:16]
    sequence = credential_lookup_sequence()
    return bytes(sequence[passcode_index(b, i)] for i, b in enumerate(raw))


def zpad(data: bytes | str, size: int) -> bytes:
    if isinstance(data, str):
        data = data.encode(errors="replace")
    return data[:size].ljust(size, b"\x00")
