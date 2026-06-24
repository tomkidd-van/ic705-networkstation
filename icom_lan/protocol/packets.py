from __future__ import annotations

import dataclasses
import struct
from typing import Iterable

from ..constants import (
    AUDIO_SIZE,
    CONNINFO_SIZE,
    DATA_SIZE,
    LOGIN_SIZE,
    OPENCLOSE_SIZE,
    STATUS_SIZE,
    TOKEN_SIZE,
    TX_AUDIO_MAX_PAYLOAD,
)
from ..models import RadioCapability
from ..auth import passcode, zpad


@dataclasses.dataclass(frozen=True)
class StreamStatus:
    status_error: int
    disc: int
    civ_remote_port: int
    audio_remote_port: int


def build_login_packet(
    *,
    my_id: int,
    remote_id: int,
    auth_seq: int,
    token_request: int,
    username: str,
    password: str,
    client_name: str,
) -> bytes:
    p = bytearray(LOGIN_SIZE)
    struct.pack_into("<I", p, 0x00, LOGIN_SIZE)
    struct.pack_into("<I", p, 0x08, my_id)
    struct.pack_into("<I", p, 0x0C, remote_id)
    struct.pack_into(">I", p, 0x10, LOGIN_SIZE - 0x10)
    p[0x14] = 0x01
    p[0x15] = 0x00
    struct.pack_into(">H", p, 0x16, auth_seq & 0xFFFF)
    struct.pack_into("<H", p, 0x1A, token_request & 0xFFFF)
    # Capture-derived login framing: controlled credential captures show
    # encoded username at 0x40:0x50, encoded password at 0x50:0x60
    # and client name at 0x60:0x70.  Username/password use the same
    # character-position-shifted encoder independently.
    p[0x40:0x50] = zpad(passcode(username), 16)
    p[0x50:0x60] = zpad(passcode(password), 16)
    p[0x60:0x70] = zpad(client_name, 16)
    return bytes(p)


def build_token_packet(
    *,
    my_id: int,
    remote_id: int,
    auth_seq: int,
    token_request: int,
    token: int,
    request_type: int,
) -> bytes:
    p = bytearray(TOKEN_SIZE)
    struct.pack_into("<I", p, 0x00, TOKEN_SIZE)
    struct.pack_into("<I", p, 0x08, my_id)
    struct.pack_into("<I", p, 0x0C, remote_id)
    struct.pack_into(">I", p, 0x10, TOKEN_SIZE - 0x10)
    p[0x14] = 0x01
    p[0x15] = request_type & 0xFF
    struct.pack_into(">H", p, 0x16, auth_seq & 0xFFFF)
    struct.pack_into("<H", p, 0x1A, token_request & 0xFFFF)
    struct.pack_into("<I", p, 0x1C, token & 0xFFFFFFFF)
    struct.pack_into(">H", p, 0x24, 0x0798)
    return bytes(p)


def build_stream_request_packet(
    *,
    my_id: int,
    remote_id: int,
    auth_seq: int,
    token_request: int,
    token: int,
    radio: RadioCapability,
    username: str,
    rx_codec: int,
    tx_codec: int,
    rx_sample_rate: int,
    tx_sample_rate: int,
    civ_local_port: int,
    audio_local_port: int,
    rx_enable: int = 1,
    tx_enable: int = 1,
    tx_buffer: int = 200,
    convert: int = 1,
) -> bytes:
    p = bytearray(CONNINFO_SIZE)
    struct.pack_into("<I", p, 0x00, CONNINFO_SIZE)
    struct.pack_into("<I", p, 0x08, my_id)
    struct.pack_into("<I", p, 0x0C, remote_id)
    struct.pack_into(">I", p, 0x10, CONNINFO_SIZE - 0x10)
    p[0x14] = 0x01
    p[0x15] = 0x03
    struct.pack_into(">H", p, 0x16, auth_seq & 0xFFFF)
    struct.pack_into("<H", p, 0x1A, token_request & 0xFFFF)
    struct.pack_into("<I", p, 0x1C, token & 0xFFFFFFFF)
    if radio.macaddress is not None:
        struct.pack_into("<H", p, 0x27, 0x8010)
        p[0x2A:0x30] = radio.macaddress[:6]
    elif radio.guid is not None:
        p[0x20:0x30] = radio.guid[:16]
    p[0x40:0x60] = zpad(radio.name, 32)
    p[0x60:0x70] = zpad(passcode(username), 16)
    # CAP-001/CAP-002 confirm the default values below as an accepted IC-705
    # baseline request shape.  v93 exposes lab-only overrides for CAP-011
    # stream-request A/B tests so we can derive which fields are mandatory,
    # optional or merely tolerated without editing source between captures.
    p[0x70] = rx_enable & 0xFF
    p[0x71] = tx_enable & 0xFF
    p[0x72] = rx_codec & 0xFF
    p[0x73] = tx_codec & 0xFF
    struct.pack_into(">I", p, 0x74, rx_sample_rate)
    struct.pack_into(">I", p, 0x78, tx_sample_rate)
    struct.pack_into(">I", p, 0x7C, civ_local_port)
    struct.pack_into(">I", p, 0x80, audio_local_port)
    struct.pack_into(">I", p, 0x84, tx_buffer & 0xFFFFFFFF)
    p[0x88] = convert & 0xFF
    return bytes(p)


def parse_stream_status_packet(data: bytes) -> StreamStatus | None:
    if len(data) != STATUS_SIZE:
        return None
    return StreamStatus(
        status_error=struct.unpack_from("<I", data, 0x30)[0],
        disc=data[0x40],
        civ_remote_port=struct.unpack_from(">H", data, 0x42)[0],
        audio_remote_port=struct.unpack_from(">H", data, 0x46)[0],
    )


def build_civ_open_close_packet(*, my_id: int, remote_id: int, stream_seq: int, close: bool = False) -> bytes:
    p = bytearray(OPENCLOSE_SIZE)
    struct.pack_into("<I", p, 0x00, OPENCLOSE_SIZE)
    struct.pack_into("<I", p, 0x08, my_id)
    struct.pack_into("<I", p, 0x0C, remote_id)
    struct.pack_into("<H", p, 0x10, 0x01C0)
    p[0x12] = 0x00
    struct.pack_into(">H", p, 0x13, stream_seq & 0xFFFF)
    p[0x15] = 0x00 if close else 0x04
    return bytes(p)


def build_civ_data_packet(*, my_id: int, remote_id: int, stream_seq: int, payload: bytes) -> bytes:
    p = bytearray(DATA_SIZE)
    struct.pack_into("<I", p, 0x00, DATA_SIZE + len(payload))
    struct.pack_into("<I", p, 0x08, my_id)
    struct.pack_into("<I", p, 0x0C, remote_id)
    p[0x10] = 0xC1
    struct.pack_into("<H", p, 0x11, len(payload) & 0xFFFF)
    struct.pack_into(">H", p, 0x13, stream_seq & 0xFFFF)
    return bytes(p) + payload


def build_audio_packet(*, my_id: int, remote_id: int, stream_seq: int, payload: bytes) -> bytes:
    p = bytearray(AUDIO_SIZE)
    struct.pack_into("<I", p, 0x00, AUDIO_SIZE + len(payload))
    struct.pack_into("<I", p, 0x08, my_id)
    struct.pack_into("<I", p, 0x0C, remote_id)
    ident = 0x9781 if len(payload) == 0xA0 else 0x0080
    struct.pack_into("<H", p, 0x10, ident)
    # CAP-004 confirms the audio stream sequence field is big-endian at
    # 0x12:0x14 for RX.  TX packet construction uses the same stream
    # sequence byte order until a TX-specific capture proves otherwise.
    struct.pack_into(">H", p, 0x12, stream_seq & 0xFFFF)
    struct.pack_into("<H", p, 0x14, 0)
    struct.pack_into(">H", p, 0x16, len(payload))
    return bytes(p) + payload


def iter_audio_packet_chunks(payload: bytes, *, max_payload: int = TX_AUDIO_MAX_PAYLOAD) -> Iterable[bytes]:
    offset = 0
    while offset < len(payload):
        partial = payload[offset:offset + max_payload]
        if partial:
            yield partial
        offset += len(partial) if partial else max_payload
