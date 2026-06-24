#!/usr/bin/env python3
"""Local-only command helpers that do not require an active radio session."""

from __future__ import annotations

import socket
from typing import Optional

from ..audio import (
    describe_sounddevice_devices as audio_describe_sounddevice_devices,
    preflight_sounddevice_device as audio_preflight_sounddevice_device,
    resolve_sounddevice_selector as audio_resolve_sounddevice_selector,
)


def list_audio_devices() -> int:
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise SystemExit("list-audio-devices requires: python3 -m pip install sounddevice") from exc

    print(sd.query_devices())
    return 0


def describe_sounddevice_devices(kind: Optional[str] = None) -> str:
    return audio_describe_sounddevice_devices(kind)


def resolve_sounddevice_selector(device: Optional[str]) -> Optional[str | int]:
    return audio_resolve_sounddevice_selector(device)


def preflight_sounddevice_device(device: Optional[str], kind: str, label: str) -> None:
    audio_preflight_sounddevice_device(device, kind, label)


def rigctl_selftest(host: str, port: int, command: str, timeout: float = 3.0) -> int:
    payload = command if command.endswith("\n") else command + "\n"
    print(f"Connecting to {host}:{port}")
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        print(f">>> {payload.rstrip()}")
        sock.sendall(payload.encode("ascii"))
        chunks: list[bytes] = []
        while True:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
            if b"\n" in data:
                # Most first-pass replies are one or two lines. Keep the test quick
                # rather than waiting for the server to close.
                break
    response = b"".join(chunks).decode("ascii", errors="replace")
    print("<<<", response.rstrip() if response else "(no response)")
    return 0
