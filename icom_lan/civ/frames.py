from __future__ import annotations

import struct

from ..constants import DATA_SIZE


def build_civ_frame(command: bytes, radio_civ: int, controller_addr: int = 0xE1) -> bytes:
    """Build an Icom CI-V frame for controller-to-radio traffic."""
    return bytes([0xFE, 0xFE, radio_civ & 0xFF, controller_addr & 0xFF]) + command + bytes([0xFD])


def extract_civ_frames(packet: bytes) -> list[bytes]:
    """Extract CI-V frames from an Icom LAN CI-V data packet or raw payload."""
    frames: list[bytes] = []
    if len(packet) >= DATA_SIZE and packet[0x10] == 0xC1:
        datalen = struct.unpack_from("<H", packet, 0x11)[0]
        payload = packet[DATA_SIZE:DATA_SIZE + datalen]
    else:
        payload = packet

    start = 0
    while True:
        try:
            i = payload.index(b"\xfe\xfe", start)
            j = payload.index(b"\xfd", i + 2)
        except ValueError:
            break
        frames.append(payload[i:j + 1])
        start = j + 1
    return frames
