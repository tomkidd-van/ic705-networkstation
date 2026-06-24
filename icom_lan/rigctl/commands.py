from __future__ import annotations

RIGCTL_ALIASES: dict[str, str] = {
    "get_ptt": "t",
    "set_ptt": "T",
    "get_freq": "f",
    "set_freq": "F",
    "get_mode": "m",
    "set_mode": "M",
    "get_split_vfo": "s",
    "set_split_vfo": "S",
    "get_split_freq": "i",
    "set_split_freq": "I",
    "get_split_mode": "x",
    "set_split_mode": "X",
    "get_split_freq_mode": "k",
    "set_split_freq_mode": "K",
    "get_vfo": "v",
    "set_vfo": "V",
    "set_rit": "J",
    "get_rit": "j",
    "set_xit": "Z",
    "get_xit": "z",
    "set_ts": "N",
    "get_ts": "n",
    "set_func": "U",
    "get_func": "u",
    "set_level": "L",
    "get_level": "l",
    "set_parm": "P",
    "get_parm": "p",
    "set_ant": "Y",
    "get_ant": "y",
    "set_rptr_shift": "R",
    "get_rptr_shift": "r",
    "set_rptr_offs": "O",
    "get_rptr_offs": "o",
    "set_ctcss_tone": "C",
    "get_ctcss_tone": "c",
    "set_dcs_code": "D",
    "get_dcs_code": "d",
    "set_ctcss_sql": "set_ctcss_sql",
    "get_ctcss_sql": "get_ctcss_sql",
    "set_dcs_sql": "set_dcs_sql",
    "get_dcs_sql": "get_dcs_sql",
    "set_trn": "A",
    "get_trn": "a",
    "get_dcd": "get_dcd",
    "send_dtmf": "send_dtmf",
    "recv_dtmf": "recv_dtmf",
    "send_morse": "b",
    "stop_morse": "stop_morse",
    "wait_morse": "wait_morse",
    "send_voice_mem": "send_voice_mem",
    "vfo_op": "G",
    "scan": "g",
    "set_channel": "H",
    "get_channel": "h",
    "set_bank": "B",
    "set_mem": "E",
    "get_mem": "e",
    "reset": "*",
    "get_info": "_",
    "get_powerstat": "get_powerstat",
    "set_powerstat": "set_powerstat",
    "dump_state": "dump_state",
    "dump_caps": "dump_caps",
    "chk_vfo": "chk_vfo",
    "set_vfo_opt": "set_vfo_opt",
    "get_vfo_info": "get_vfo_info",
    "get_rig_info": "get_rig_info",
    "get_vfo_list": "get_vfo_list",
    "get_modes": "get_modes",
    "get_clock": "get_clock",
    "set_clock": "set_clock",
    "get_mode_bandwidths": "get_mode_bandwidths",
    "set_separator": "set_separator",
    "get_separator": "get_separator",
    "set_lock_mode": "set_lock_mode",
    "get_lock_mode": "get_lock_mode",
    "set_cache": "set_cache",
    "get_cache": "get_cache",
    "set_twiddle": "set_twiddle",
    "get_twiddle": "get_twiddle",
    "uplink": "uplink",
    "halt": "halt",
    "password": "password",
    "send_raw": "send_raw",
    "client_version": "client_version",
    "power2mW": "power2mW",
    "mW2power": "mW2power",
    "send_cmd": "w",
    "send_cmd_rx": "W",
    "pause": "pause",
    "q": "q",
    "Q": "Q",
}


def normalize_rigctl_command(line: str) -> tuple[str, list[str], bool]:
    """Normalize common rigctld command spellings.

    Important Hamlib distinction:
      \\long_name selects a long command name
      +command requests extended response format

    A backslash is not an extended-response request. Treating it as one breaks
    Hamlib NET rigctl startup because \\dump_state is parsed as a strict
    multi-line capability stream.
    """
    cmdline = line.strip()
    extended = False

    if not cmdline or cmdline.startswith("#"):
        return ("", [], extended)

    if cmdline.startswith("+"):
        extended = True
        cmdline = cmdline[1:].lstrip()

    while cmdline and cmdline[0] in ";|,":
        extended = True
        cmdline = cmdline[1:].lstrip()

    if cmdline.startswith("\\"):
        cmdline = cmdline[1:].lstrip()

    parts = cmdline.split()
    if not parts:
        return ("", [], extended)

    cmd = parts[0]
    args = parts[1:]
    return (RIGCTL_ALIASES.get(cmd, cmd), args, extended)
