from __future__ import annotations

import struct

from ..constants import CAPABILITIES_SIZE, RADIO_CAP_SIZE
from ..models import RadioCapability


def parse_radio_capabilities(data: bytes) -> list[RadioCapability]:
    """Parse Icom LAN capability packets into RadioCapability records."""
    if len(data) < CAPABILITIES_SIZE:
        return []
    if (len(data) - CAPABILITIES_SIZE) % RADIO_CAP_SIZE != 0:
        return []

    # CAP-001 through CAP-006 showed the IC-705 single-radio capability
    # packet carrying bytes 00 01 at offsets 0x40:0x42.  Treat this count as
    # big-endian.  The old little-endian interpretation happened to work for
    # the one-radio case only because the packet length limited iteration.
    num_radios = struct.unpack_from(">H", data, 0x40)[0]
    radios: list[RadioCapability] = []
    end = min(len(data), CAPABILITIES_SIZE + num_radios * RADIO_CAP_SIZE)
    for off in range(CAPABILITIES_SIZE, end, RADIO_CAP_SIZE):
        chunk = data[off:off + RADIO_CAP_SIZE]
        commoncap = struct.unpack_from("<H", chunk, 0x07)[0]
        mac = chunk[0x0A:0x10]
        guid = chunk[0x00:0x10]
        name = chunk[0x10:0x30].split(b"\x00", 1)[0].decode(errors="replace")
        audio = chunk[0x30:0x50].split(b"\x00", 1)[0].decode(errors="replace")
        civ_addr = chunk[0x52]
        rxsample = struct.unpack_from("<H", chunk, 0x53)[0]
        txsample = struct.unpack_from("<H", chunk, 0x55)[0]
        baudrate_be = struct.unpack_from(">I", chunk, 0x5A)[0]
        if commoncap == 0x8010:
            radios.append(RadioCapability(name, audio, civ_addr, rxsample, txsample, baudrate_be, commoncap, mac, None))
        else:
            radios.append(RadioCapability(name, audio, civ_addr, rxsample, txsample, baudrate_be, None, None, guid))
    return radios
