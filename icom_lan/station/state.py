"""Station runtime-state initialization helpers.

These helpers keep the station defaults in one place while leaving the live
station loop, rigctl server and audio bridge behavior owned by IcomLanSession.
"""

from __future__ import annotations

import time
from typing import Any


_RIGCTL_SCALAR_DEFAULTS: dict[str, object] = {
    "rigctl_vfo": "VFOA",
    "rigctl_freq_hz": 0,
    "rigctl_mode": "",
    "rigctl_width": 0,
    "rigctl_split": False,
    "rigctl_split_vfo": "VFOB",
    "rigctl_split_freq_hz": 146_520_000,
    "rigctl_split_mode": "USB",
    "rigctl_split_width": 0,
    "rigctl_rit_hz": 0,
    "rigctl_xit_hz": 0,
    "rigctl_ts_hz": 100,
    "rigctl_ant": 1,
    "rigctl_ant_option": 0,
    "rigctl_rptr_shift": "None",
    "rigctl_rptr_offs": 0,
    "rigctl_ctcss_tone": 0,
    "rigctl_dcs_code": 0,
    "rigctl_ctcss_sql": 0,
    "rigctl_dcs_sql": 0,
    "rigctl_dtmf": "",
    "rigctl_separator": "\n",
    "rigctl_lock_mode": 0,
    "rigctl_cache_timeout_ms": 0,
    "rigctl_twiddle_timeout_s": 0,
    "rigctl_clock": "",
    "rigctl_uplink": 0,
    "rigctl_trn": "OFF",
    "rigctl_powerstat": 1,
    "rigctl_bank": 0,
    "rigctl_mem": 0,
    "rigctl_client_version": "",
}


_RIGCTL_DICT_NAMES = (
    "rigctl_func",
    "rigctl_level",
    "rigctl_parm",
)


def reset_rigctl_cached_state(session: Any) -> None:
    """Reset cached/non-authoritative rigctl facade state."""
    for name, value in _RIGCTL_SCALAR_DEFAULTS.items():
        setattr(session, name, value)
    for name in _RIGCTL_DICT_NAMES:
        setattr(session, name, {})


def initialize_station_runtime_state(
    session: Any,
    *,
    rigctld_strict: bool,
    allow_real_tune: bool,
    real_cat_cache_ttl: float,
) -> None:
    """Initialize station runtime state at the start of run_station()."""
    session.ptt_state = False
    session.ptt_radio_state = False
    # CAP-009 showed that using radio PTT readback state directly as the
    # station TX audio gate can leave a short window where one or more local
    # audio blocks are packetized after a T 0 command is sent but before the
    # radio PTT-off readback completes.  Keep the local audio gate separate
    # from the last confirmed radio PTT state: T 1 opens the gate only after
    # radio PTT-on confirmation, while T 0 closes the gate before sending
    # radio PTT-off.
    session.tx_audio_gate_enabled = False
    session.rigctl_strict = bool(rigctld_strict)
    session.allow_real_tune = bool(allow_real_tune)
    session.real_cat_cache_ttl = max(0.0, float(real_cat_cache_ttl))
    session.rigctl_freq_valid = False
    session.rigctl_mode_valid = False
    session.station_started_at = time.time()
    session.station_last_control_ping = 0.0
    session.station_last_idle_control = 0.0
    session.station_last_token_keepalive = 0.0
    session.rigctl_category_counts.clear()
    session.station_counters.clear()
    session.rigctl_freq_read_at = 0.0
    session.rigctl_mode_read_at = 0.0
    reset_rigctl_cached_state(session)
