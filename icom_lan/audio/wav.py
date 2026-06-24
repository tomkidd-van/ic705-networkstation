from __future__ import annotations

import time
import wave
from pathlib import Path
from typing import Iterator, Optional

from ..errors import ProtocolError


def inspect_wav_file(
    wav_path: str,
    *,
    expect_rate: Optional[int],
    print_summary: bool = True,
) -> dict[str, object]:
    """Inspect and validate a WAV file for direct TX use.

    The TX path intentionally does not resample or convert audio. A valid file
    is mono, uncompressed PCM, 16-bit sample width and matches expect_rate when
    an expected sample rate is supplied.
    """
    path = Path(wav_path)
    if not path.exists():
        raise ProtocolError(f"WAV file does not exist: {wav_path}")
    if not path.is_file():
        raise ProtocolError(f"WAV path is not a file: {wav_path}")

    try:
        with wave.open(str(path), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            frames = wf.getnframes()
            comptype = wf.getcomptype()
            compname = wf.getcompname()
    except wave.Error as exc:
        raise ProtocolError(f"Not a readable PCM WAV file: {exc}") from exc

    duration = frames / float(sample_rate) if sample_rate else 0.0
    expected_rate = int(expect_rate if expect_rate is not None else sample_rate)

    issues: list[str] = []
    if comptype != "NONE":
        issues.append(f"compression is {comptype!r}/{compname!r}; expected uncompressed PCM")
    if channels != 1:
        issues.append(f"channels={channels}; expected mono/1")
    if sample_width != 2:
        issues.append(f"sample_width={sample_width} bytes; expected 2 bytes / 16-bit PCM")
    if sample_rate != expected_rate:
        issues.append(f"sample_rate={sample_rate}; expected {expected_rate}")
    if frames <= 0:
        issues.append("file has no audio frames")

    info: dict[str, object] = {
        "path": str(path),
        "channels": channels,
        "sample_width_bytes": sample_width,
        "sample_rate_hz": sample_rate,
        "frames": frames,
        "duration_seconds": duration,
        "compression": comptype,
        "compression_name": compname,
        "expected_sample_rate_hz": expected_rate,
        "valid_for_tx": not issues,
        "issues": issues,
    }

    if print_summary:
        print("WAV inspect:")
        print(f"  file: {path}")
        print(f"  channels: {channels}")
        print(f"  sample width: {sample_width} byte(s)")
        print(f"  sample rate: {sample_rate} Hz")
        print(f"  frames: {frames}")
        print(f"  duration: {duration:.3f} s")
        print(f"  compression: {comptype} ({compname})")
        print(f"  expected TX sample rate: {expected_rate} Hz")
        if issues:
            print("  valid for direct tx-wav: no")
            print("  issues:")
            for issue in issues:
                print(f"    - {issue}")
        else:
            print("  valid for direct tx-wav: yes")
    return info


def wav_pcm16le_chunks(
    wav_path: str,
    *,
    sample_rate: int,
    block_frames: int = 320,
    validate: bool = True,
    verbose: bool = False,
) -> Iterator[bytes]:
    """Yield mono PCM16LE chunks from a WAV file, paced by sample_rate."""
    if validate:
        info = inspect_wav_file(wav_path, expect_rate=sample_rate, print_summary=verbose)
        if not bool(info["valid_for_tx"]):
            issues = "; ".join(str(x) for x in info["issues"])
            raise ProtocolError(f"WAV is not valid for direct tx-wav: {issues}")
    with wave.open(wav_path, "rb") as wf:
        while True:
            data = wf.readframes(block_frames)
            if not data:
                break
            yield data
            out_frames = len(data) // 2
            time.sleep(out_frames / float(sample_rate))


def write_wav_s16le(path: str, pcm_s16le: bytes, sample_rate: int) -> None:
    """Write mono signed 16-bit little-endian PCM bytes as a WAV file."""
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_s16le)
