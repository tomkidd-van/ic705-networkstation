from __future__ import annotations

import contextlib
import time
from typing import Optional

from ..constants import SCRIPT_VERSION


def handle_rigctl_startup_compat(
    session,
    cmd: str,
    args: list[str],
    extended: bool,
    *,
    radio_civ: Optional[int] = None,
) -> Optional[tuple[str, bool]]:
    """Handle Hamlib startup and introspection compatibility commands."""
    if cmd == "get_powerstat":
        return (session.rigctl_response(value=str(getattr(session, "rigctl_powerstat", 1)), extended=extended), False)

    if cmd == "set_powerstat":
        if args:
            try:
                session.rigctl_powerstat = int(float(args[-1]))
                # Accept/cache power requests but do not power-cycle the radio.
                return (session.rigctl_response(True, extended=extended, report=True), False)
            except ValueError:
                pass
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "chk_vfo":
        # Hamlib netrigctl_open() parses the first response line with sscanf("%d").
        return ("0\n", False)

    if cmd == "dump_state":
        # Never append RPRT here.  Hamlib model 2 reads a strict capability
        # stream from \dump_state during open().
        return (session.rigctl_dump_state(), False)

    if cmd == "dump_caps":
        response = session.rigctl_dump_caps()
        if extended:
            response += "RPRT 0\n"
        return (response, False)

    if cmd == "get_rig_info":
        return (session.rigctl_response(value=f"IC-705 Python Icom LAN station {SCRIPT_VERSION}", extended=extended), False)

    if cmd == "get_vfo_list":
        return (session.rigctl_response(value="VFOA VFOB", extended=extended), False)

    if cmd == "get_modes":
        return (session.rigctl_response(value="LSB USB CW CWR RTTY RTTYR AM FM DV", extended=extended), False)

    if cmd == "get_vfo_info":
        response = session.rigctl_vfo_info()
        if extended:
            response += "RPRT 0\n"
        return (response, False)

    if cmd == "client_version":
        session.rigctl_client_version = " ".join(args) if args else ""
        return (session.rigctl_response(True, extended=extended, report=True), False)

    return None



def handle_rigctl_station_state(
    session,
    cmd: str,
    args: list[str],
    extended: bool,
    *,
    radio_civ: Optional[int] = None,
) -> Optional[tuple[str, bool]]:
    """Handle cached station state commands that are not yet real CI-V operations."""
    if cmd == "v":
        return (session.rigctl_response(value=getattr(session, "rigctl_vfo", "VFOA"), extended=extended), False)

    if cmd == "V":
        if args:
            session.rigctl_vfo = args[-1]
            return (session.rigctl_response(True, extended=extended, report=True), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "k":
        split_mode = getattr(session, "rigctl_split_mode", "") or getattr(session, "rigctl_mode", "") or "USB"
        value = "\n".join([
            str(getattr(session, "rigctl_split_freq_hz", 0)),
            split_mode,
            str(getattr(session, "rigctl_split_width", getattr(session, "rigctl_width", 0))),
        ])
        return (session.rigctl_response(value=value, extended=extended), False)

    if cmd == "K":
        if len(args) >= 3:
            with contextlib.suppress(ValueError):
                session.rigctl_split_freq_hz = int(float(args[0]))
            session.rigctl_split_mode = args[1]
            with contextlib.suppress(ValueError):
                session.rigctl_split_width = int(float(args[2]))
            return (session.rigctl_response(True, extended=extended, report=True), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "s":
        # Hamlib get_split_vfo returns split flag and TX VFO.  rigctl model 2
        # probes this during open/command setup.  Report no split by default.
        split = "1" if getattr(session, "rigctl_split", False) else "0"
        tx_vfo = getattr(session, "rigctl_split_vfo", "VFOB")
        value = f"{split}\n{tx_vfo}"
        return (session.rigctl_response(value=value, extended=extended), False)

    if cmd == "S":
        # set_split_vfo split tx_vfo
        if args:
            session.rigctl_split = args[0] not in ("0", "None", "none", "OFF", "off")
            if len(args) > 1:
                session.rigctl_split_vfo = args[-1]
            return (session.rigctl_response(True, extended=extended, report=True), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "i":
        # get_split_freq: cached placeholder.
        return (session.rigctl_response(value=str(getattr(session, "rigctl_split_freq_hz", 0)), extended=extended), False)

    if cmd == "I":
        if args:
            try:
                session.rigctl_split_freq_hz = int(float(args[-1]))
                return (session.rigctl_response(True, extended=extended, report=True), False)
            except ValueError:
                pass
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "x":
        # get_split_mode: mode and width on separate lines, which is the
        # standard rigctl multi-value pattern.  Avoid returning a blank mode:
        # some clients display that as an empty first line.
        mode = str(getattr(session, "rigctl_split_mode", "") or getattr(session, "rigctl_mode", "") or "USB")
        if not mode.strip():
            mode = "USB"
        width = getattr(session, "rigctl_split_width", getattr(session, "rigctl_width", 0))
        session.log("rigctl split mode get", repr(mode), width)
        return (session.rigctl_response(value=f"{mode}\n{width}", extended=extended), False)

    if cmd == "X":
        if args and args[0] == "?":
            return (session.rigctl_response(value=session.rigctl_query_list("modes"), extended=extended), False)
        if args:
            session.rigctl_split_mode = args[0]
            if len(args) > 1:
                with contextlib.suppress(ValueError):
                    session.rigctl_split_width = int(float(args[1]))
            return (session.rigctl_response(True, extended=extended, report=True), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "J":
        if args:
            try:
                session.rigctl_rit_hz = int(float(args[-1]))
                return (session.rigctl_response(True, extended=extended, report=True), False)
            except ValueError:
                pass
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "j":
        return (session.rigctl_response(value=str(getattr(session, "rigctl_rit_hz", 0)), extended=extended), False)

    if cmd == "Z":
        if args:
            try:
                session.rigctl_xit_hz = int(float(args[-1]))
                return (session.rigctl_response(True, extended=extended, report=True), False)
            except ValueError:
                pass
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "z":
        return (session.rigctl_response(value=str(getattr(session, "rigctl_xit_hz", 0)), extended=extended), False)

    if cmd == "N":
        if args:
            try:
                session.rigctl_ts_hz = int(float(args[-1]))
                return (session.rigctl_response(True, extended=extended, report=True), False)
            except ValueError:
                pass
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "n":
        return (session.rigctl_response(value=str(getattr(session, "rigctl_ts_hz", 100)), extended=extended), False)

    if cmd == "Y":
        if args:
            try:
                session.rigctl_ant = int(float(args[0]))
                if len(args) > 1:
                    session.rigctl_ant_option = int(float(args[1]))
                return (session.rigctl_response(True, extended=extended, report=True), False)
            except ValueError:
                pass
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "y":
        value = f"{getattr(session, 'rigctl_ant', 1)}\n{getattr(session, 'rigctl_ant_option', 0)}"
        return (session.rigctl_response(value=value, extended=extended), False)

    if cmd == "R":
        if args:
            session.rigctl_rptr_shift = args[-1]
            return (session.rigctl_response(True, extended=extended, report=True), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "r":
        return (session.rigctl_response(value=getattr(session, "rigctl_rptr_shift", "None"), extended=extended), False)

    if cmd == "O":
        if args:
            try:
                session.rigctl_rptr_offs = int(float(args[-1]))
                return (session.rigctl_response(True, extended=extended, report=True), False)
            except ValueError:
                pass
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "o":
        return (session.rigctl_response(value=str(getattr(session, "rigctl_rptr_offs", 0)), extended=extended), False)

    if cmd == "C":
        if args:
            try:
                session.rigctl_ctcss_tone = int(float(args[-1]))
                return (session.rigctl_response(True, extended=extended, report=True), False)
            except ValueError:
                pass
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "c":
        return (session.rigctl_response(value=str(getattr(session, "rigctl_ctcss_tone", 0)), extended=extended), False)

    if cmd == "D":
        if args:
            try:
                session.rigctl_dcs_code = int(float(args[-1]))
                return (session.rigctl_response(True, extended=extended, report=True), False)
            except ValueError:
                pass
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "d":
        return (session.rigctl_response(value=str(getattr(session, "rigctl_dcs_code", 0)), extended=extended), False)

    if cmd == "A":
        if args and args[0] == "?":
            return (session.rigctl_response(value="OFF RIG POLL", extended=extended), False)
        if args:
            session.rigctl_trn = args[-1]
            return (session.rigctl_response(True, extended=extended, report=True), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "a":
        return (session.rigctl_response(value=getattr(session, "rigctl_trn", "OFF"), extended=extended), False)

    if cmd == "get_dcd":
        return (session.rigctl_response(value="0", extended=extended), False)

    if cmd == "b":
        # send_morse is accepted as a no-op compatibility command.
        return (session.rigctl_response(True, extended=extended, report=True), False)

    if cmd == "G":
        if args and args[0] == "?":
            return (session.rigctl_response(value=session.rigctl_query_list("vfo_ops"), extended=extended), False)
        return (session.rigctl_response(True, extended=extended, report=True), False)

    if cmd == "g":
        if args and args[0] == "?":
            return (session.rigctl_response(value=session.rigctl_query_list("scan"), extended=extended), False)
        return (session.rigctl_response(True, extended=extended, report=True), False)

    if cmd == "B":
        if args:
            with contextlib.suppress(ValueError):
                session.rigctl_bank = int(float(args[-1]))
            return (session.rigctl_response(True, extended=extended, report=True), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "E":
        if args:
            with contextlib.suppress(ValueError):
                session.rigctl_mem = int(float(args[-1]))
            return (session.rigctl_response(True, extended=extended, report=True), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "e":
        return (session.rigctl_response(value=str(getattr(session, "rigctl_mem", 0)), extended=extended), False)

    if cmd == "H":
        # set_channel/get_channel are complex memory operations; accept as no-op.
        return (session.rigctl_response(True, extended=extended, report=True), False)

    if cmd == "h":
        return (session.rigctl_response(value="0", extended=extended), False)

    if cmd == "*":
        # Do not reset the radio from the facade.
        return (session.rigctl_response(True, extended=extended, report=True), False)

    if cmd == "_":
        return (session.rigctl_response(value=f"IC-705 Python Icom LAN station {SCRIPT_VERSION}", extended=extended), False)

    return None



def handle_rigctl_cached_families(
    session,
    cmd: str,
    args: list[str],
    extended: bool,
    *,
    radio_civ: Optional[int] = None,
) -> Optional[tuple[str, bool]]:
    """Handle cached function, level, parameter and tone/DTMF command families."""
    if cmd == "U":
        if args and args[0] == "?":
            return (session.rigctl_response(value=session.rigctl_query_list("funcs"), extended=extended), False)
        if len(args) >= 2:
            session.rigctl_set_cached(session.rigctl_func, args[0], args[-1])
            return (session.rigctl_response(True, extended=extended, report=True), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "u":
        if args and args[0] == "?":
            return (session.rigctl_response(value=session.rigctl_query_list("funcs"), extended=extended), False)
        if args:
            return (session.rigctl_response(value=session.rigctl_get_cached(session.rigctl_func, args[0], "0"), extended=extended), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "L":
        if args and args[0] == "?":
            return (session.rigctl_response(value=session.rigctl_query_list("levels"), extended=extended), False)
        if len(args) >= 2:
            session.rigctl_set_cached(session.rigctl_level, args[0], args[-1])
            return (session.rigctl_response(True, extended=extended, report=True), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "l":
        if args and args[0] == "?":
            return (session.rigctl_response(value=session.rigctl_query_list("levels"), extended=extended), False)
        if args:
            return (session.rigctl_response(value=session.rigctl_get_cached(session.rigctl_level, args[0], "0"), extended=extended), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "P":
        if args and args[0] == "?":
            return (session.rigctl_response(value=session.rigctl_query_list("parms"), extended=extended), False)
        if len(args) >= 2:
            session.rigctl_set_cached(session.rigctl_parm, args[0], args[-1])
            return (session.rigctl_response(True, extended=extended, report=True), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "p":
        if args and args[0] == "?":
            return (session.rigctl_response(value=session.rigctl_query_list("parms"), extended=extended), False)
        if args:
            return (session.rigctl_response(value=session.rigctl_get_cached(session.rigctl_parm, args[0], "0"), extended=extended), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "set_ctcss_sql":
        if args:
            with contextlib.suppress(ValueError):
                session.rigctl_ctcss_sql = int(float(args[-1]))
                return (session.rigctl_response(True, extended=extended, report=True), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "get_ctcss_sql":
        return (session.rigctl_response(value=str(getattr(session, "rigctl_ctcss_sql", 0)), extended=extended), False)

    if cmd == "set_dcs_sql":
        if args:
            with contextlib.suppress(ValueError):
                session.rigctl_dcs_sql = int(float(args[-1]))
                return (session.rigctl_response(True, extended=extended, report=True), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "get_dcs_sql":
        return (session.rigctl_response(value=str(getattr(session, "rigctl_dcs_sql", 0)), extended=extended), False)

    if cmd == "send_dtmf":
        session.rigctl_dtmf = " ".join(args)
        return (session.rigctl_response(True, extended=extended, report=True), False)

    if cmd == "recv_dtmf":
        return (session.rigctl_response(value=getattr(session, "rigctl_dtmf", ""), extended=extended), False)

    if cmd in ("stop_morse", "wait_morse", "send_voice_mem"):
        return (session.rigctl_response(True, extended=extended, report=True), False)

    return None



def handle_rigctl_misc_compat(
    session,
    cmd: str,
    args: list[str],
    extended: bool,
    *,
    radio_civ: Optional[int] = None,
) -> Optional[tuple[str, bool]]:
    """Handle miscellaneous no-op or local compatibility commands."""
    if cmd == "set_vfo_opt":
        return (session.rigctl_response(True, extended=extended, report=True), False)

    if cmd == "power2mW":
        # Linear placeholder: 0.0..1.0 maps to 0..10000 mW.
        try:
            watts_ratio = float(args[0]) if args else 0.0
            return (session.rigctl_response(value=str(int(max(0.0, min(1.0, watts_ratio)) * 10000)), extended=extended), False)
        except ValueError:
            return (session.rigctl_response(False, extended=extended), False)

    if cmd == "mW2power":
        try:
            mw = float(args[0]) if args else 0.0
            return (session.rigctl_response(value=str(max(0.0, min(1.0, mw / 10000.0))), extended=extended), False)
        except ValueError:
            return (session.rigctl_response(False, extended=extended), False)

    if cmd == "w":
        # Raw send_cmd is intentionally not forwarded to the radio.
        return (session.rigctl_response(value="", extended=extended), False)

    if cmd == "W":
        return (session.rigctl_response(value="", extended=extended), False)

    if cmd == "pause":
        if args:
            with contextlib.suppress(ValueError):
                time.sleep(max(0.0, min(float(args[-1]), 10.0)))
        return (session.rigctl_response(True, extended=extended, report=True), False)

    if cmd == "get_clock":
        if not getattr(session, "rigctl_clock", ""):
            session.rigctl_clock = time.strftime("%Y%m%d%H%M%S.000%z")
        return (session.rigctl_response(value=session.rigctl_clock, extended=extended), False)

    if cmd == "set_clock":
        session.rigctl_clock = " ".join(args)
        return (session.rigctl_response(True, extended=extended, report=True), False)

    if cmd == "get_mode_bandwidths":
        mode = args[0].upper() if args else getattr(session, "rigctl_mode", "USB").upper()
        table = {
            "USB": "3000 2400 1800",
            "LSB": "3000 2400 1800",
            "AM": "9000 6000 3000",
            "CW": "1200 500 250",
            "CWR": "1200 500 250",
            "FM": "15000 10000 7000",
            "DV": "15000 10000 7000",
            "RTTY": "2400 500 250",
            "RTTYR": "2400 500 250",
        }
        return (session.rigctl_response(value=table.get(mode, "3000 2400 1800"), extended=extended), False)

    if cmd == "set_separator":
        if args:
            session.rigctl_separator = args[0]
            return (session.rigctl_response(True, extended=extended, report=True), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "get_separator":
        return (session.rigctl_response(value=getattr(session, "rigctl_separator", "\\n"), extended=extended), False)

    if cmd == "set_lock_mode":
        if args:
            with contextlib.suppress(ValueError):
                session.rigctl_lock_mode = int(float(args[-1]))
                return (session.rigctl_response(True, extended=extended, report=True), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "get_lock_mode":
        # Hamlib 4.6.2 may pause after this startup probe if it receives only
        # the value line.  Include an explicit RPRT completion marker for this
        # long-form helper while keeping the value first for parsers that read it.
        return (session.rigctl_response(value=str(getattr(session, "rigctl_lock_mode", 0)), extended=extended, report=True), False)

    if cmd == "set_cache":
        if args:
            with contextlib.suppress(ValueError):
                session.rigctl_cache_timeout_ms = int(float(args[-1]))
                return (session.rigctl_response(True, extended=extended, report=True), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "get_cache":
        return (session.rigctl_response(value=str(getattr(session, "rigctl_cache_timeout_ms", 0)), extended=extended), False)

    if cmd == "set_twiddle":
        if args:
            with contextlib.suppress(ValueError):
                session.rigctl_twiddle_timeout_s = int(float(args[-1]))
                return (session.rigctl_response(True, extended=extended, report=True), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "get_twiddle":
        return (session.rigctl_response(value=str(getattr(session, "rigctl_twiddle_timeout_s", 0)), extended=extended), False)

    if cmd == "uplink":
        if args:
            with contextlib.suppress(ValueError):
                session.rigctl_uplink = int(float(args[-1]))
                return (session.rigctl_response(True, extended=extended, report=True), False)
        return (session.rigctl_response(False, extended=extended), False)

    if cmd == "password":
        # No authentication layer for the local facade.
        return (session.rigctl_response(True, extended=extended, report=True), False)

    if cmd == "halt":
        # Match the rigctld command but only stop our station server; do not
        # send any radio power-down/reset command.
        return (session.rigctl_response(True, extended=extended, report=True), True)

    if cmd == "send_raw":
        return (session.rigctl_response(value="", extended=extended), False)

    if cmd == "q":
        return ("", True)

    if cmd == "Q":
        return (session.rigctl_response(True, extended=extended, report=True), True)

    return None



def handle_rigctl_line(session, line: str, *, radio_civ: Optional[int] = None) -> tuple[str, bool]:
    """Handle a rigctld-compatible subset of station commands.

    The dispatch is intentionally grouped so future rigctl expansion can add
    real CI-V implementations without disturbing Hamlib startup compatibility
    or cached/no-op station behavior.
    """
    cmd, args, extended = session.normalize_rigctl_command(line)
    if not cmd:
        return ("", False)

    session.log("rigctl command:", line.strip(), "=>", cmd, args, "extended=", extended)

    # General stationkeeping rule: while we own the radio LAN session, every
    # command path should service pending control traffic so ping requests are
    # not left queued until some later shutdown or audio loop.
    with contextlib.suppress(Exception):
        session.service_control_stationkeeping(max_packets=50)

    for category, handler, strict_allowed in (
        ("real-cat", session.handle_rigctl_real_cat, True),
        ("startup-compat", session.handle_rigctl_startup_compat, True),
        ("cached-state", session.handle_rigctl_station_state, False),
        ("cached-family", session.handle_rigctl_cached_families, False),
        ("misc-compat", session.handle_rigctl_misc_compat, False),
    ):
        result = handler(cmd, args, extended, radio_civ=radio_civ)
        if result is not None:
            with contextlib.suppress(Exception):
                session.service_control_stationkeeping(max_packets=50)
            # Real-cat commands self-report category because some cache-only
            # F/M branches are intentionally strict-rejectable.  All other
            # handler groups are classified here.
            if category != "real-cat":
                result = session.rigctl_category_result(category, cmd, result, strict_allowed=strict_allowed, extended=extended)
            return result

    session.log("unsupported rigctl command:", line.strip())
    with contextlib.suppress(Exception):
        session.service_control_stationkeeping(max_packets=50)
    return (session.rigctl_response(False, extended=extended), False)


