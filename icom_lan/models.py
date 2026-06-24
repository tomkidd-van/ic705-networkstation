from __future__ import annotations

import dataclasses
from typing import Optional


@dataclasses.dataclass
class ConnInfoControl:
    packet_len: int
    packet_type: int
    sequence: int
    sentid: int
    rcvdid: int
    token_request: int
    token: int
    field_20_u32: int
    field_24_u16: int
    commoncap_le: int
    mac: Optional[bytes]
    name: Optional[str]
    raw: bytes

    @property
    def mac_text(self) -> Optional[str]:
        if not self.mac:
            return None
        return ":".join(f"{b:02x}" for b in self.mac)

    def summary(self) -> str:
        parts = [
            f"seq={self.sequence}",
            f"sentid=0x{self.sentid:08x}",
            f"rcvdid=0x{self.rcvdid:08x}",
            f"token_request=0x{self.token_request:04x}",
            f"token=0x{self.token:08x}",
            f"field_20_u32=0x{self.field_20_u32:08x}",
            f"field_24_u16=0x{self.field_24_u16:04x}",
            f"commoncap_le=0x{self.commoncap_le:04x}",
        ]
        if self.mac_text:
            parts.append(f"mac={self.mac_text}")
        if self.name:
            parts.append(f"name={self.name}")
        return " ".join(parts)


@dataclasses.dataclass
class RadioCapability:
    name: str
    audio: str
    civ_addr: int
    rxsample_mask: int
    txsample_mask: int
    baudrate_be: int
    commoncap: Optional[int] = None
    macaddress: Optional[bytes] = None
    guid: Optional[bytes] = None

