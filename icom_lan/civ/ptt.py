from __future__ import annotations

from typing import Optional


def decode_ptt_reply_body(body: bytes) -> Optional[bool]:
    """Decode body bytes from a CI-V 1C PTT reply when recognizable."""
    # Expected IC-705 body starts with subcommand 00 then state.
    if len(body) >= 2 and body[0] == 0x00 and body[1] in (0x00, 0x01):
        return body[1] == 0x01
    # Some Icom replies may include only a state byte after command 1C.
    if len(body) >= 1 and body[-1] in (0x00, 0x01):
        return body[-1] == 0x01
    return None
