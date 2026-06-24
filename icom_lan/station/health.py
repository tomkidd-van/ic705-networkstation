"""Station counters and health-summary helpers."""

from __future__ import annotations

import time
from typing import Any, MutableMapping, Optional

RigctlResult = Optional[tuple[str, bool]]


def bump_counter(counters: MutableMapping[str, int], name: str, amount: int = 1) -> None:
    counters[name] = counters.get(name, 0) + amount


def build_station_health_summary(session: Any) -> str:
    parts = [
        f"uptime_s={int(time.time() - session.station_started_at)}",
        f"ptt={int(bool(getattr(session, 'ptt_radio_state', False)))}",
        f"freq={getattr(session, 'rigctl_freq_hz', 0)}",
        f"mode={getattr(session, 'rigctl_mode', '')}",
    ]
    with_context = getattr(session, "session_observation_summary", None)
    if callable(with_context):
        summary = with_context()
        if summary:
            parts.append(summary)
    for key, value in sorted(session.rigctl_category_counts.items()):
        parts.append(f"rigctl.{key}={value}")
    for key, value in sorted(session.station_counters.items()):
        parts.append(f"{key}={value}")
    return " ".join(parts)


def record_rigctl_category(
    session: Any,
    category: str,
    cmd: str,
    result: RigctlResult,
    *,
    real: bool = False,
    strict_allowed: bool = True,
    extended: bool = False,
) -> RigctlResult:
    session.rigctl_category_counts[category] = session.rigctl_category_counts.get(category, 0) + 1
    session.log(f"rigctl category={category} cmd={cmd} strict_allowed={int(strict_allowed)} real={int(real)}")
    if result is not None and session.rigctl_strict and not strict_allowed:
        session.log(f"rigctl strict reject cmd={cmd} category={category}")
        return (session.rigctl_response(False, extended=extended), False)
    return result
