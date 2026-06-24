"""sounddevice selector and preflight helpers."""

from __future__ import annotations

from typing import Optional

from ..errors import ProtocolError


def describe_sounddevice_devices(kind: Optional[str] = None) -> str:
    try:
        import sounddevice as sd
    except ImportError:
        return "sounddevice is not installed"

    try:
        devices = sd.query_devices()
    except Exception as exc:
        return f"could not query devices: {exc}"

    lines: list[str] = []
    for idx, dev in enumerate(devices):
        name = str(dev.get("name", ""))
        max_in = int(dev.get("max_input_channels", 0))
        max_out = int(dev.get("max_output_channels", 0))
        if kind == "input" and max_in <= 0:
            continue
        if kind == "output" and max_out <= 0:
            continue
        lines.append(f"  [{idx}] {name!r} input={max_in} output={max_out}")
    return "\n".join(lines) if lines else f"no {kind or 'audio'} devices reported"


def resolve_sounddevice_selector(device: Optional[str]) -> Optional[str | int]:
    """Convert CLI device selectors to the form sounddevice expects.

    argparse returns command-line values as strings. sounddevice treats "1" as
    a device-name substring, not device index 1, so digit-only selectors must be
    converted to int before query_devices()/RawInputStream()/RawOutputStream().
    """
    if device is None:
        return None
    value = str(device).strip()
    if value == "":
        return None
    if value.isdigit():
        return int(value)
    return device


def preflight_sounddevice_device(device: Optional[str], kind: str, label: str) -> None:
    """Validate a sounddevice input/output selector before starting bridge threads."""
    selector = resolve_sounddevice_selector(device)
    if selector is None:
        return
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise SystemExit(f"{label} requires: python3 -m pip install sounddevice") from exc

    try:
        sd.query_devices(selector, kind=kind)
        return
    except Exception as exc:
        available = describe_sounddevice_devices(kind)
        raise ProtocolError(
            f"{label}: no {kind} device matching {device!r} (resolved selector {selector!r}). "
            "Run 'python3 icom_lan.py list-audio-devices' and use the exact device name or numeric index.\n"
            f"Available {kind} devices:\n{available}"
        ) from exc
