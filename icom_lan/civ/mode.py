from __future__ import annotations

CIV_MODE_NAMES = {
    0x00: "LSB",
    0x01: "USB",
    0x02: "AM",
    0x03: "CW",
    0x04: "RTTY",
    0x05: "FM",
    0x06: "WFM",
    0x07: "CWR",
    0x08: "RTTYR",
    0x17: "DV",
}

CIV_MODE_CODES = {name: code for code, name in CIV_MODE_NAMES.items()}


def normalize_rigctl_mode_name(mode: str) -> str:
    """Normalize Hamlib/rigctl mode spellings to CI-V mode table names."""
    mode = mode.strip().upper()
    aliases = {
        "PKTFM": "FM",
        "PKTUSB": "USB",
        "PKTLSB": "LSB",
        "D-STAR": "DV",
        "DSTAR": "DV",
    }
    return aliases.get(mode, mode)
