from __future__ import annotations


def pcm16le_apply_gain(payload: bytes, gain: float) -> bytes:
    """Apply fixed scalar gain to signed little-endian int16 PCM bytes.

    This is intentionally dependency-free and preserves odd trailing bytes.
    A gain of 1.0 returns the original object unchanged.
    """
    if gain == 1.0:
        return payload
    out = bytearray(len(payload))
    limit = len(payload) - (len(payload) % 2)
    for i in range(0, limit, 2):
        sample = int.from_bytes(payload[i:i + 2], "little", signed=True)
        scaled = int(round(sample * gain))
        if scaled > 32767:
            scaled = 32767
        elif scaled < -32768:
            scaled = -32768
        out[i:i + 2] = int(scaled).to_bytes(2, "little", signed=True)
    if len(payload) % 2:
        out[-1] = payload[-1]
    return bytes(out)


class Pcm16MonoRateAdapter:
    """Small stateful mono PCM16LE linear resampler.

    The station bridge has two sample-rate domains: the radio/network stream
    rate negotiated with the IC-705 and the local audio-device rate used by
    Windows/ALSA/PortAudio.  Keeping this dependency-free avoids adding a hard
    scipy/samplerate requirement for the station path.
    """

    def __init__(self, source_rate: int, target_rate: int):
        self.source_rate = int(source_rate)
        self.target_rate = int(target_rate)
        if self.source_rate <= 0 or self.target_rate <= 0:
            raise ValueError("sample rates must be positive")
        self.enabled = self.source_rate != self.target_rate
        self.step = self.source_rate / float(self.target_rate)
        self._buffer: list[int] = []
        self._pos = 0.0

    def process(self, payload: bytes) -> bytes:
        if not payload:
            return payload
        if len(payload) % 2:
            payload = payload[:-1]
        if not payload or not self.enabled:
            return payload

        sample_count = len(payload) // 2
        self._buffer.extend(
            int.from_bytes(payload[i:i + 2], "little", signed=True)
            for i in range(0, sample_count * 2, 2)
        )

        out = bytearray()
        # Need one sample ahead for interpolation.  We intentionally retain the
        # final input sample across calls so packet boundaries stay continuous.
        while self._pos + 1.0 < len(self._buffer):
            base = int(self._pos)
            frac = self._pos - base
            s0 = self._buffer[base]
            s1 = self._buffer[base + 1]
            sample = int(round(s0 + (s1 - s0) * frac))
            if sample > 32767:
                sample = 32767
            elif sample < -32768:
                sample = -32768
            out += sample.to_bytes(2, "little", signed=True)
            self._pos += self.step

        drop = int(self._pos)
        if drop > 0:
            del self._buffer[:drop]
            self._pos -= drop

        # Bound memory if a host API feeds tiny/non-contiguous fragments.
        # Keeping a couple of samples is enough for the next interpolation.
        if len(self._buffer) > max(4, self.source_rate * 2):
            keep = self._buffer[-2:]
            self._buffer = keep
            self._pos = min(self._pos, max(0.0, len(self._buffer) - 1.0))

        return bytes(out)
