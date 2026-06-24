from __future__ import annotations

import contextlib
import time
from typing import Optional

from ..constants import (
    CIV_FIRST_READ_RETRY_TIMEOUT,
    CIV_PTT_READ_TIMEOUT,
    CIV_READ_TIMEOUT,
    CIV_SET_ACK_TIMEOUT,
    PTT_OFF_RECOVERY_ATTEMPTS,
    PTT_OFF_RECOVERY_DELAY,
)
from .frequency import decode_civ_bcd_frequency, encode_civ_bcd_frequency
from .mode import CIV_MODE_CODES, CIV_MODE_NAMES, normalize_rigctl_mode_name
from .ptt import decode_ptt_reply_body


def _radio_civ(session, radio_civ: Optional[int]) -> int:
    if radio_civ is not None:
        return radio_civ
    return session.selected_radio.civ_addr if session.selected_radio is not None else 0xA4


def read_frequency_civ(session, radio_civ: Optional[int] = None, timeout: float = CIV_READ_TIMEOUT) -> Optional[int]:
    """Read current radio frequency with CI-V command 0x03."""
    if session.civ is None:
        return None
    radio_civ = _radio_civ(session, radio_civ)
    with session.civ_lock:
        rx_frame = session.civ_query_frame(bytes([0x03]), radio_civ=radio_civ, expect_command=0x03, timeout=timeout)
        if rx_frame is None:
            session.log("CI-V frequency read timeout/no parseable reply")
            return None
        freq = decode_civ_bcd_frequency(rx_frame[5:-1])
        if freq is not None:
            session.log("CI-V frequency decoded", freq)
            return freq
        session.log("CI-V frequency reply did not decode", rx_frame.hex(" "))
        return None


def set_frequency_civ(session, freq_hz: int, radio_civ: Optional[int] = None, timeout: float = CIV_SET_ACK_TIMEOUT) -> bool:
    """Set current radio frequency with CI-V command 0x05 and read back."""
    if session.civ is None:
        return False
    radio_civ = _radio_civ(session, radio_civ)

    payload = bytes([0x05]) + encode_civ_bcd_frequency(freq_hz)
    with session.civ_lock:
        session.log("CI-V frequency set", freq_hz)
        ack_ok = session.civ_command_ack(payload, radio_civ=radio_civ, timeout=timeout)
        if not ack_ok:
            session.log("CI-V frequency set acknowledgement unavailable or negative")
        readback = session.read_frequency_civ(radio_civ=radio_civ, timeout=timeout)
    if readback is not None:
        session.rigctl_freq_hz = readback
        session.rigctl_freq_valid = True
        session.rigctl_freq_read_at = time.time()
        ok = abs(readback - freq_hz) <= 1
        session.log("CI-V frequency set readback", readback, "ok" if ok else "mismatch")
        return ok
    session.log("CI-V frequency set readback unavailable")
    return False


def set_mode_civ(
    session,
    mode: str,
    width: int = 0,
    radio_civ: Optional[int] = None,
    timeout: float = CIV_SET_ACK_TIMEOUT,
) -> bool:
    """Set current radio mode with CI-V command 0x06 and read back."""
    if session.civ is None:
        return False
    radio_civ = _radio_civ(session, radio_civ)

    mode_norm = normalize_rigctl_mode_name(mode)
    mode_code = CIV_MODE_CODES.get(mode_norm)
    if mode_code is None:
        session.log("CI-V mode set rejected unknown mode", mode)
        return False

    with session.civ_lock:
        session.log("CI-V mode set", mode_norm, f"0x{mode_code:02x}")
        ack_ok = session.civ_command_ack(bytes([0x06, mode_code]), radio_civ=radio_civ, timeout=timeout)
        if not ack_ok:
            session.log("CI-V mode set acknowledgement unavailable or negative")
        readback = session.read_mode_civ(radio_civ=radio_civ, timeout=timeout)
    if readback is not None:
        read_mode, read_width = readback
        session.rigctl_mode = read_mode
        session.rigctl_width = int(width) if width else read_width
        session.rigctl_mode_valid = True
        session.rigctl_mode_read_at = time.time()
        ok = read_mode == mode_norm
        session.log("CI-V mode set readback", read_mode, "ok" if ok else "mismatch")
        return ok
    session.log("CI-V mode set readback unavailable")
    return False


def read_mode_civ(session, radio_civ: Optional[int] = None, timeout: float = CIV_READ_TIMEOUT) -> Optional[tuple[str, int]]:
    """Read current radio operating mode with CI-V command 0x04."""
    if session.civ is None:
        return None
    radio_civ = _radio_civ(session, radio_civ)

    with session.civ_lock:
        rx_frame = session.civ_query_frame(bytes([0x04]), radio_civ=radio_civ, expect_command=0x04, timeout=timeout)
        if rx_frame is None:
            session.log("CI-V mode read timeout/no parseable reply")
            return None
        body = rx_frame[5:-1]
        if not body:
            return None
        mode_code = body[0]
        mode = CIV_MODE_NAMES.get(mode_code)
        if mode is None:
            session.log("CI-V mode code unknown", f"0x{mode_code:02x}", "body", body.hex(" "))
            return None
        filter_code = body[1] if len(body) >= 2 else None
        if filter_code is not None:
            session.log("CI-V mode decoded", mode, f"filter=0x{filter_code:02x}")
        else:
            session.log("CI-V mode decoded", mode, "filter=<none>")
        width = int(getattr(session, "rigctl_width", 0))
        return (mode, width)


def read_ptt_civ(session, radio_civ: Optional[int] = None, timeout: float = CIV_PTT_READ_TIMEOUT) -> Optional[bool]:
    """Read TX/PTT state with CI-V 1C 00 when the radio responds to it."""
    if session.civ is None:
        return None
    radio_civ = _radio_civ(session, radio_civ)
    with session.civ_lock:
        rx_frame = session.civ_query_frame(bytes([0x1C, 0x00]), radio_civ=radio_civ, expect_command=0x1C, timeout=timeout)
        if rx_frame is None:
            session.log("CI-V PTT read timeout/no parseable reply")
            return None
        body = rx_frame[5:-1]
        ptt = decode_ptt_reply_body(body)
        if ptt is not None:
            session.ptt_radio_state = ptt
            session.ptt_state = ptt
            session.log("CI-V PTT decoded", int(ptt), "body", body.hex(" "))
            return ptt
        session.log("CI-V PTT reply did not decode", rx_frame.hex(" "))
        return None


def ptt(session, enabled: bool, radio_civ: Optional[int] = None) -> None:
    """Send CI-V PTT command without assuming the resulting radio state."""
    cmd = bytes([0x1C, 0x00, 0x01 if enabled else 0x00])
    with session.civ_lock:
        with session.civ_exchange():
            frame = session.civ_frame(cmd, radio_civ=radio_civ)
            session.log("PTT", "ON" if enabled else "OFF", frame.hex(" "))
            session.send_civ_payload(frame)
            session.bump_station_counter("ptt_set_tx")


def send_ptt_off_recovery(
    session,
    *,
    radio_civ: Optional[int] = None,
    attempts: int = PTT_OFF_RECOVERY_ATTEMPTS,
    delay: float = PTT_OFF_RECOVERY_DELAY,
    confirm: bool = True,
    timeout: float = CIV_PTT_READ_TIMEOUT,
    reason: str = "unspecified",
) -> Optional[bool]:
    """Best-effort PTT-OFF recovery that does not depend on readback.

    A late/failed CI-V PTT readback can leave ambiguity: the radio may have
    accepted the preceding PTT ON/OFF command even though the readback timed
    out.  For safety, always close the local TX audio gate first and send
    several fire-and-forget PTT OFF commands.  A final readback is only used to
    update internal state; OFF has already been sent regardless of readback.
    """
    session.tx_audio_gate_enabled = False
    session.ptt_state = False
    session.log(
        "PTT OFF recovery start",
        f"reason={reason}",
        f"attempts={attempts}",
    )
    for idx in range(max(1, int(attempts))):
        try:
            ptt(session, False, radio_civ=radio_civ)
            session.log("PTT OFF recovery command sent", f"attempt={idx + 1}")
        except Exception as exc:  # pragma: no cover - defensive safety path
            session.log("PTT OFF recovery command failed", f"attempt={idx + 1}", repr(exc))
        if idx + 1 < max(1, int(attempts)):
            time.sleep(max(0.0, float(delay)))

    if not confirm:
        session.ptt_radio_state = False
        return None

    actual = read_ptt_civ(session, radio_civ=radio_civ, timeout=timeout)
    if actual is False:
        session.ptt_radio_state = False
        session.ptt_state = False
        session.tx_audio_gate_enabled = False
        session.log("PTT OFF recovery confirmed radio PTT OFF")
        return False
    if actual is True:
        session.ptt_radio_state = True
        session.log("PTT OFF recovery readback still indicates radio PTT ON")
        return True

    session.ptt_radio_state = None
    session.log("PTT OFF recovery readback unavailable after OFF commands")
    return None


def set_ptt_and_confirm(
    session,
    enabled: bool,
    *,
    radio_civ: Optional[int] = None,
    timeout: float = CIV_PTT_READ_TIMEOUT,
) -> bool:
    """Set PTT and require radio readback confirmation.

    The set/readback sequence is serialized as one CI-V critical section.
    This avoids an interleaved rigctl/CAT request from another TCP client
    landing between T 1/T 0 and the authority readback.
    """
    with session.civ_lock:
        with session.civ_exchange():
            ptt(session, enabled, radio_civ=radio_civ)
            time.sleep(0.05)
            actual = read_ptt_civ(session, radio_civ=radio_civ, timeout=timeout)
    if actual is enabled:
        session.ptt_radio_state = actual
        session.ptt_state = actual
        if not enabled:
            session.tx_audio_gate_enabled = False
        session.log("PTT readback confirmed", int(actual))
        return True

    session.log(
        "PTT readback did not confirm requested state",
        f"requested={int(enabled)}",
        f"actual={'unknown' if actual is None else int(actual)}",
    )
    if enabled:
        session.tx_audio_gate_enabled = False
        session.log("PTT ON not confirmed; sending repeated safety PTT OFF")
        with contextlib.suppress(Exception):
            send_ptt_off_recovery(
                session,
                radio_civ=radio_civ,
                timeout=timeout,
                reason="ptt-on-not-confirmed",
            )
    else:
        session.tx_audio_gate_enabled = False
        session.log("PTT OFF not confirmed; repeating PTT OFF safety commands")
        with contextlib.suppress(Exception):
            send_ptt_off_recovery(
                session,
                radio_civ=radio_civ,
                timeout=timeout,
                reason="ptt-off-not-confirmed",
            )
    return False


def handle_rigctl_real_cat(
    session,
    cmd: str,
    args: list[str],
    extended: bool,
    *,
    radio_civ: Optional[int] = None,
) -> Optional[tuple[str, bool]]:
    """Handle real CI-V CAT/PTT commands: f/F, m/M and T/t."""
    if cmd == "T":
        if not args:
            return session.rigctl_category_result("real-cat", cmd, (session.rigctl_response(False, extended=extended), False), real=True, extended=extended)
        value = args[-1]
        if value not in ("0", "1"):
            return session.rigctl_category_result("real-cat", cmd, (session.rigctl_response(False, extended=extended), False), real=True, extended=extended)
        enabled = value == "1"
        # CAP-009 showed a short window where the station TX audio thread could
        # keep sending local audio blocks after T 0 was issued because it was
        # gated by the last confirmed radio PTT state.  Close the local audio
        # gate before sending radio PTT OFF; open it only after radio PTT ON is
        # confirmed.
        if not enabled:
            session.tx_audio_gate_enabled = False
            session.log("station TX audio gate requested closed before radio PTT OFF")
        else:
            session.tx_audio_gate_enabled = False
        ok = session.set_ptt_and_confirm(enabled, radio_civ=radio_civ)
        if enabled:
            session.tx_audio_gate_enabled = bool(ok)
            if ok:
                session.log("station TX audio gate enabled after radio PTT ON confirmation")
            else:
                session.log("station TX audio gate remains closed; radio PTT ON was not confirmed")
        session.bump_station_counter("rigctl_ptt_set")
        return session.rigctl_category_result("real-cat", cmd, (session.rigctl_response(ok, extended=extended, report=True), False), real=True, extended=extended)

    if cmd == "t":
        ptt_radio = session.read_ptt_civ(radio_civ=radio_civ)
        session.bump_station_counter("rigctl_ptt_read")
        if ptt_radio is None:
            session.log("rigctl t radio PTT readback unavailable; no internal fallback returned")
            return session.rigctl_category_result("real-cat", cmd, (session.rigctl_response(False, extended=extended), False), real=True, extended=extended)
        return session.rigctl_category_result("real-cat", cmd, (session.rigctl_response(value="1" if ptt_radio else "0", extended=extended), False), real=True, extended=extended)

    if cmd == "f":
        now = time.time()
        cache_ttl = float(getattr(session, "real_cat_cache_ttl", 0.0))
        cached_valid = bool(getattr(session, "rigctl_freq_valid", False))
        cached_freq = getattr(session, "rigctl_freq_hz", 0)
        cache_age = now - getattr(session, "rigctl_freq_read_at", 0.0)
        if cache_ttl > 0 and cached_valid and cache_age <= cache_ttl:
            freq = cached_freq
            session.log("CI-V frequency cache hit", freq, f"age={cache_age:.3f}s")
        else:
            freq = session.read_frequency_civ(radio_civ=radio_civ)
            if freq is None and not cached_valid:
                session.log("CI-V frequency first read failed; retrying once")
                freq = session.read_frequency_civ(radio_civ=radio_civ, timeout=CIV_FIRST_READ_RETRY_TIMEOUT)
            if freq is not None:
                session.rigctl_freq_hz = freq
                session.rigctl_freq_valid = True
                session.rigctl_freq_read_at = time.time()
            else:
                session.log("CI-V frequency unavailable; no synthetic fallback returned")
                return session.rigctl_category_result("real-cat", cmd, (session.rigctl_response(False, extended=extended), False), real=True, extended=extended)
        return session.rigctl_category_result("real-cat", cmd, (session.rigctl_response(value=str(freq), extended=extended), False), real=True, extended=extended)

    if cmd == "F":
        if args:
            try:
                freq_hz = int(float(args[-1]))
            except ValueError:
                session.log("set frequency received invalid argument", args[-1])
                return session.rigctl_category_result("real-cat", cmd, (session.rigctl_response(False, extended=extended), False), real=True, extended=extended)

            if getattr(session, "allow_real_tune", False):
                ok = session.set_frequency_civ(freq_hz, radio_civ=radio_civ)
                return session.rigctl_category_result("real-cat", cmd, (session.rigctl_response(ok, extended=extended, report=True), False), real=True, extended=extended)

            session.rigctl_freq_hz = freq_hz
            session.log("cached rigctl frequency set", session.rigctl_freq_hz, "(real tune disabled)")
            return session.rigctl_category_result("real-cat", cmd, (session.rigctl_response(True, extended=extended, report=True), False), real=True, strict_allowed=False, extended=extended)
        session.log("set frequency called without a frequency argument")
        return session.rigctl_category_result("real-cat", cmd, (session.rigctl_response(False, extended=extended), False), real=True, extended=extended)

    if cmd == "m":
        now = time.time()
        cache_ttl = float(getattr(session, "real_cat_cache_ttl", 0.0))
        cached_valid = bool(getattr(session, "rigctl_mode_valid", False))
        cached_mode = getattr(session, "rigctl_mode", "")
        cached_width = getattr(session, "rigctl_width", 0)
        cache_age = now - getattr(session, "rigctl_mode_read_at", 0.0)
        if cache_ttl > 0 and cached_valid and cache_age <= cache_ttl:
            mode, width = cached_mode, cached_width
            session.log("CI-V mode cache hit", mode, width, f"age={cache_age:.3f}s")
        else:
            result = session.read_mode_civ(radio_civ=radio_civ)
            if result is None and not cached_valid:
                session.log("CI-V mode first read failed; retrying once")
                result = session.read_mode_civ(radio_civ=radio_civ, timeout=CIV_FIRST_READ_RETRY_TIMEOUT)
            if result is not None:
                mode, width = result
                session.rigctl_mode = mode
                session.rigctl_width = width
                session.rigctl_mode_valid = True
                session.rigctl_mode_read_at = time.time()
            else:
                session.log("CI-V mode unavailable; no synthetic fallback returned")
                return session.rigctl_category_result("real-cat", cmd, (session.rigctl_response(False, extended=extended), False), real=True, extended=extended)
        value = f"{mode}\n{width}"
        return session.rigctl_category_result("real-cat", cmd, (session.rigctl_response(value=value, extended=extended), False), real=True, extended=extended)

    if cmd == "M":
        if args and args[0] == "?":
            return session.rigctl_category_result("real-cat", cmd, (session.rigctl_response(value=session.rigctl_query_list("modes"), extended=extended), False), real=True, strict_allowed=True, extended=extended)
        if args:
            mode = args[0]
            width = getattr(session, "rigctl_width", 0)
            if len(args) > 1:
                with contextlib.suppress(ValueError):
                    width = int(float(args[1]))

            if getattr(session, "allow_real_tune", False):
                ok = session.set_mode_civ(mode, width=width, radio_civ=radio_civ)
                return session.rigctl_category_result("real-cat", cmd, (session.rigctl_response(ok, extended=extended, report=True), False), real=True, extended=extended)

            session.rigctl_mode = normalize_rigctl_mode_name(mode)
            session.rigctl_width = width
            session.log("cached rigctl mode set", session.rigctl_mode, getattr(session, "rigctl_width", 0), "(real tune disabled)")
            return session.rigctl_category_result("real-cat", cmd, (session.rigctl_response(True, extended=extended, report=True), False), real=True, strict_allowed=False, extended=extended)
        return session.rigctl_category_result("real-cat", cmd, (session.rigctl_response(False, extended=extended), False), real=True, extended=extended)

    return None
