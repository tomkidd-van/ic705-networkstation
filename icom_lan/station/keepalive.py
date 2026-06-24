"""Station keepalive helper.

The live scheduling policy remains unchanged; this function is called by the
IcomLanSession method so existing call sites keep the same method name.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any, Callable

from ..constants import (
    STATION_CONTROL_PING_INTERVAL,
    STATION_IDLE_CONTROL_INTERVAL,
    STATION_TOKEN_KEEPALIVE_INTERVAL,
)


def _try_stationkeeping_action(session: Any, counter: str, action: Callable[[], None]) -> bool:
    try:
        action()
    except Exception as exc:  # pragma: no cover - exercised by offline smoke with a fake session
        with contextlib.suppress(Exception):
            session.bump_station_counter(f"{counter}_failed")
        with contextlib.suppress(Exception):
            session.log("station keepalive action failed", counter, repr(exc))
        return False
    with contextlib.suppress(Exception):
        session.bump_station_counter(counter)
    return True


def keepalive_tick(session: Any, *, force: bool = False) -> None:
    if session.control is None:
        return
    now = time.time()

    with contextlib.suppress(Exception):
        session.service_control_stationkeeping(max_packets=50)

    if not force and getattr(session, "civ_exchange_depth", 0) > 0:
        session.bump_station_counter("keepalive_deferred_for_cat")
        with contextlib.suppress(Exception):
            session.service_control_stationkeeping(max_packets=50)
        return

    if force or now - session.station_last_control_ping >= STATION_CONTROL_PING_INTERVAL:
        if _try_stationkeeping_action(session, "control_ping_tx", session.control.send_ping_request):
            session.station_last_control_ping = now

    if force or now - session.station_last_idle_control >= STATION_IDLE_CONTROL_INTERVAL:
        if _try_stationkeeping_action(
            session,
            "control_idle_tx",
            lambda: session.control.send_control(0x00, tracked=True, seq=0),
        ):
            session.station_last_idle_control = now

    if force or now - session.station_last_token_keepalive >= STATION_TOKEN_KEEPALIVE_INTERVAL:
        if _try_stationkeeping_action(session, "token_keepalive_tx", lambda: session.send_token(0x05)):
            session.station_last_token_keepalive = now

    with contextlib.suppress(Exception):
        session.service_control_stationkeeping(max_packets=50)
