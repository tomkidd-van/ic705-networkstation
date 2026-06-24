from __future__ import annotations

from .protocol.control import decode_stream_status_error


class ProtocolError(RuntimeError):
    pass


class StreamAllocationError(ProtocolError):
    """Radio returned stream status but did not allocate usable stream ports."""

    def __init__(self, status_error: int, disc: int, civ_port: int, audio_port: int):
        self.status_error = status_error
        self.disc = disc
        self.civ_port = civ_port
        self.audio_port = audio_port
        super().__init__(
            "Radio returned stream ports "
            f"{civ_port}/{audio_port}. "
            f"status_error=0x{status_error:08x} ({decode_stream_status_error(status_error)}), "
            f"disc={disc}. Stream allocation was not granted."
        )

