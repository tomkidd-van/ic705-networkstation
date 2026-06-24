from __future__ import annotations

import struct
from typing import Optional

from ..constants import (
    CONTROL_SIZE,
    PING_SIZE,
    TOKEN_SIZE,
    CONNINFO_SIZE,
    SHUTDOWN_CONTROL_HEX_LIMIT,
)
from ..models import ConnInfoControl


def decode_stream_status_error(error: int) -> str:
    # Observed values so far:
    #   0x00000000 means stream allocation accepted
    #   0xffffffff is an explicit rejection in older handling
    #   0xfcffffff appeared in CAP-011B/CAP-011G with stream ports 0/0 when
    #   either TX or RX codec was set to 0x00 in the stream request.
    #   0xfdffffff has appeared with stream ports 0/0 when the radio is not
    #   ready or another/stale stream allocation is in transition.  Endianness
    #   is odd in Icom packets, so keep both the raw hex and conservative label.
    if error == 0x00000000:
        return "ok"
    if error == 0xFFFFFFFF:
        return "rejected"
    if error == 0xFCFFFFFF:
        return "stream request invalid / codec zero rejected observed"
    if error == 0xFDFFFFFF:
        return "allocation unavailable / ports zero observed"
    return "unknown"


def short_hex(data: bytes, limit: int = SHUTDOWN_CONTROL_HEX_LIMIT) -> str:
    h = data[:limit].hex(" ")
    if len(data) > limit:
        h += f" ... (+{len(data) - limit} bytes)"
    return h


def printable_ascii_at(data: bytes, offset: int, size: int) -> Optional[str]:
    raw = data[offset:offset + size]
    raw = raw.split(b"\x00", 1)[0]
    if not raw:
        return None
    if all(32 <= b <= 126 for b in raw):
        return raw.decode("ascii", errors="replace")
    return None


def parse_conninfo_control_packet(data: bytes) -> Optional[ConnInfoControl]:
    """Parse the 144-byte control packet shape observed near shutdown.

    This mirrors the known top-level control header and extracts fields whose
    offsets match earlier login/token/capability structures.  Unknown bytes are
    deliberately left in raw for later reverse mapping.
    """
    if len(data) != CONNINFO_SIZE:
        return None

    packet_len, packet_type, sequence, sentid, rcvdid = struct.unpack_from("<IHHII", data, 0x00)
    if packet_len != CONNINFO_SIZE:
        return None

    token_request = struct.unpack_from("<H", data, 0x1A)[0]
    token = struct.unpack_from("<I", data, 0x1C)[0]
    field_20_u32 = struct.unpack_from("<I", data, 0x20)[0]
    field_24_u16 = struct.unpack_from("<H", data, 0x24)[0]
    commoncap_le = struct.unpack_from("<H", data, 0x27)[0]

    mac = None
    candidate_mac = data[0x2A:0x30]
    if len(candidate_mac) == 6 and any(candidate_mac):
        mac = candidate_mac

    name = printable_ascii_at(data, 0x40, 32)

    return ConnInfoControl(
        packet_len=packet_len,
        packet_type=packet_type,
        sequence=sequence,
        sentid=sentid,
        rcvdid=rcvdid,
        token_request=token_request,
        token=token,
        field_20_u32=field_20_u32,
        field_24_u16=field_24_u16,
        commoncap_le=commoncap_le,
        mac=mac,
        name=name,
        raw=data,
    )


def classify_control_packet(data: bytes) -> str:
    """Best-effort label for control-channel packets."""
    if len(data) < CONTROL_SIZE:
        return "short-control"
    if len(data) == TOKEN_SIZE:
        response = struct.unpack_from("<I", data, 0x30)[0]
        return f"token-response result=0x{response:x}"
    if len(data) == CONTROL_SIZE:
        return "control-16"
    if len(data) == PING_SIZE:
        return "control-21"
    if len(data) == CONNINFO_SIZE:
        info = parse_conninfo_control_packet(data)
        if info is not None:
            suffix = []
            if info.name:
                suffix.append(f"name={info.name}")
            if info.mac_text:
                suffix.append(f"mac={info.mac_text}")
            return "conninfo-control" + (f" ({' '.join(suffix)})" if suffix else "")
        return "conninfo-sized-control"
    return f"control-len-{len(data)}"


def control_packet_summary(data: bytes) -> str:
    if len(data) >= CONTROL_SIZE:
        pkt_len, typ, seq, sentid, rcvdid = struct.unpack_from("<IHHII", data, 0)
        return (
            f"{classify_control_packet(data)} len={len(data)} pkt_len={pkt_len} "
            f"type=0x{typ:02x} seq={seq} sentid=0x{sentid:08x} rcvdid=0x{rcvdid:08x}"
        )
    return f"{classify_control_packet(data)} len={len(data)} hex={short_hex(data)}"


def summarize_control_packets(counts: dict[tuple[int, int], int]) -> str:
    return ", ".join(
        f"len={length}/type=0x{ptype:02x}: {count}"
        for (length, ptype), count in sorted(counts.items())
    )
