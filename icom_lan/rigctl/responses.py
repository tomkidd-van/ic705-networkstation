from __future__ import annotations

from typing import Optional

RIGCTL_QUERY_LISTS: dict[str, str] = {
    "modes": "AM CW USB LSB RTTY FM WFM CWR RTTYR PKTLSB PKTUSB PKTFM D-STAR PSK PSKR DV",
    "funcs": "FAGC NB COMP VOX TONE TSQL SBKIN FBKIN ANF NR MON LOCK MUTE RIT XIT",
    "levels": "PREAMP ATT VOXDELAY AF RF SQL IF APF NR PBT_IN PBT_OUT CWPITCH RFPOWER MICGAIN KEYSPD NOTCHF COMP AGC BKINDL VOXGAIN ANTIVOX SWR ALC STRENGTH RFPOWER_METER VD_METER ID_METER MONITOR_GAIN TEMP_METER",
    "parms": "ANN BACKLIGHT BEEP TIME BAT KEYLIGHT",
    "vfos": "VFOA VFOB currVFO Main Sub TX RX",
    "vfo_ops": "CPY XCHG FROM_VFO TO_VFO UP DOWN BAND_UP BAND_DOWN",
    "scan": "STOP MEM SLCT PRIO PROG VFO",
}


def format_response(
    ok: bool = True,
    value: Optional[str] = None,
    *,
    extended: bool = False,
    report: Optional[bool] = None,
) -> str:
    """Format a Hamlib rigctld-style response."""
    if report is None:
        report = extended or not ok or value is None

    if value is not None:
        out = f"{value}\n"
        if report:
            out += "RPRT 0\n" if ok else "RPRT -1\n"
        return out

    return "RPRT 0\n" if ok else "RPRT -1\n"


def dump_state() -> str:
    """Return a minimal Hamlib NET rigctl protocol-0 dump_state stream."""
    lines = [
        "0",                    # protocol version
        "2",                    # ignored/model placeholder read by netrigctl_open()
        "2",                    # ITU region placeholder
        "0 0 0x0 0 0 0 0",      # RX frequency ranges terminator
        "0 0 0x0 0 0 0 0",      # TX frequency ranges terminator
        "0x0 0",                # tuning steps terminator
        "0x0 0",                # filters terminator
        "0",                    # max_rit
        "0",                    # max_xit
        "0",                    # max_ifshift
        "0",                    # announces
        "0 0 0 0 0 0 0",        # preamp list
        "0 0 0 0 0 0 0",        # attenuator list
        "0x0",                  # has_get_func
        "0x0",                  # has_set_func
        "0x0",                  # has_get_level
        "0x0",                  # has_set_level
        "0x0",                  # has_get_parm
        "0x0",                  # has_set_parm
    ]
    return "\n".join(lines) + "\n"


def dump_caps(script_version: str) -> str:
    return "\n".join([
        "Caps dump for model: IC-705 via Python Icom LAN station",
        "Model name: IC-705",
        "Mfg name: Icom",
        f"Backend version: {script_version}",
        "Rig type: Transceiver",
        "PTT type: CI-V over Icom LAN",
        "DCD type: None",
        "Port type: Network",
    ]) + "\n"


def vfo_info(freq: int | float | str, mode: str, width: int | float | str, vfo: str) -> str:
    return f"{vfo} {freq} {mode} {width} 0 0 0\n"


def query_list(kind: str) -> str:
    return RIGCTL_QUERY_LISTS.get(kind, "")


def bool_value(value: str) -> bool:
    return value not in ("0", "false", "False", "off", "OFF", "None", "none", "")


def parse_number(value: str) -> float:
    return float(value)


def int_string(value: float | int) -> str:
    return str(int(float(value)))


def get_cached(store: dict[str, str], key: str, default: str = "0") -> str:
    return store.get(key.upper(), default)


def set_cached(store: dict[str, str], key: str, value: str) -> None:
    store[key.upper()] = value
