from __future__ import annotations

from typing import Optional


def decode_civ_bcd_frequency(payload: bytes) -> Optional[int]:
    """Decode Icom CI-V 5-byte little-endian BCD frequency payload."""
    if len(payload) < 5:
        return None
    value = 0
    multiplier = 1
    for byte in payload[:5]:
        lo = byte & 0x0F
        hi = (byte >> 4) & 0x0F
        if lo > 9 or hi > 9:
            return None
        value += lo * multiplier
        multiplier *= 10
        value += hi * multiplier
        multiplier *= 10
    return value


def encode_civ_bcd_frequency(freq_hz: int) -> bytes:
    """Encode frequency in Hz as Icom CI-V 5-byte little-endian BCD."""
    if freq_hz < 0 or freq_hz > 9999999999:
        raise ValueError("frequency out of CI-V BCD range")
    digits = f"{freq_hz:010d}"[::-1]
    out = bytearray()
    for i in range(0, 10, 2):
        lo = int(digits[i])
        hi = int(digits[i + 1])
        out.append((hi << 4) | lo)
    return bytes(out)
