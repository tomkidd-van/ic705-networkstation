"""Station runner and rigctl TCP handler."""

from __future__ import annotations

import contextlib
import socketserver
import threading
import time
from typing import Any, Optional

from ..audio import preflight_sounddevice_device
from ..constants import (
    REAL_CAT_CACHE_TTL,
    SCRIPT_VERSION,
    STATION_CONTROL_PING_INTERVAL,
    STATION_HEALTH_INTERVAL,
    STATION_IDLE_CONTROL_INTERVAL,
    STATION_TOKEN_KEEPALIVE_INTERVAL,
    TOKEN_REMOVAL_ACK_TIMEOUT,
    TOKEN_REMOVAL_ATTEMPTS,
)
from .state import initialize_station_runtime_state


class ThreadingRigctlServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _fold_endpoint_service_counts(session: Any) -> None:
    counts: dict[str, int] = {}
    with contextlib.suppress(Exception):
        counts = session.collect_endpoint_service_counts()
    if not counts:
        return
    for key, value in counts.items():
        with contextlib.suppress(Exception):
            session.bump_station_counter(key, value)


def run_station(
    session: Any,
    stop: threading.Event,
    *,
    rigctld_listen: str,
    rigctld_port: int,
    radio_civ: Optional[int] = None,
    rigctld_handshake: bool = False,
    rigctld_debug_bytes: bool = False,
    rigctld_strict: bool = False,
    allow_real_tune: bool = False,
    real_cat_cache_ttl: float = REAL_CAT_CACHE_TTL,
    station_rx_audio: bool = False,
    station_rx_audio_device: Optional[str] = None,
    station_rx_output_gain: Optional[float] = None,
    station_rx_device_sample_rate: Optional[int] = None,
    station_tx_audio: bool = False,
    station_tx_audio_device: Optional[str] = None,
    station_tx_input_gain: float = 1.0,
    station_tx_device_sample_rate: Optional[int] = None,
) -> None:
    """Run station rigctl server and optional RX/TX audio bridge threads."""
    initialize_station_runtime_state(
        session,
        rigctld_strict=rigctld_strict,
        allow_real_tune=allow_real_tune,
        real_cat_cache_ttl=real_cat_cache_ttl,
    )
    with contextlib.suppress(Exception):
        session.set_session_phase("station:starting")
    session.civ_prime_for_station(radio_civ=radio_civ)

    class RigctlHandler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            peer = self.client_address
            session.log("rigctl client connected", peer)

            if rigctld_handshake:
                # Not all rigctld clients want a banner, so this is opt-in.
                # Useful for diagnosing clients that expect server text before
                # sending their first command.
                self.wfile.write(b"rigctld Python-IcomLAN v28\n")
                self.wfile.flush()

            while not stop.is_set():
                try:
                    raw = self.rfile.readline()
                except (ConnectionResetError, BrokenPipeError, OSError) as exc:
                    session.log("rigctl client closed/reset connection:", repr(exc))
                    break
                if not raw:
                    break
                if rigctld_debug_bytes:
                    session.log("rigctl raw bytes:", raw.hex(" "), repr(raw))
                line = ""
                try:
                    line = raw.decode("utf-8", errors="replace").strip()
                    response, close_client = session.handle_rigctl_line(line, radio_civ=radio_civ)
                except Exception as exc:
                    with contextlib.suppress(Exception):
                        session.record_session_error("rigctl:command", exc, detail=line)
                    session.log("rigctl command failed:", repr(exc))
                    response, close_client = session.rigctl_response(False), False
                if response:
                    try:
                        if rigctld_debug_bytes:
                            session.log("rigctl response bytes:", response.encode("ascii").hex(" "), repr(response))
                        self.wfile.write(response.encode("ascii"))
                        self.wfile.flush()
                    except (ConnectionResetError, BrokenPipeError, OSError) as exc:
                        session.log("rigctl response write failed:", repr(exc))
                        break
                if close_client:
                    if line == "Q":
                        stop.set()
                    break
            session.log("rigctl client disconnected", peer)

    server = ThreadingRigctlServer((rigctld_listen, rigctld_port), RigctlHandler)
    server.timeout = 0.25
    with contextlib.suppress(Exception):
        session.set_session_phase("station:listening")
    print(f"Station script version: {SCRIPT_VERSION}")
    print("Real CI-V read enabled: rigctl f queries radio frequency with cache fallback")
    print("Real CI-V read enabled: rigctl m queries radio mode with cache fallback")
    if session.real_cat_cache_ttl > 0:
        print(f"Real CI-V read cache TTL: {session.real_cat_cache_ttl:.2f}s")
    else:
        print("Real CI-V read cache: disabled; f/m require live radio replies")
    print(f"Token removal ACK timeout: {TOKEN_REMOVAL_ACK_TIMEOUT:.2f}s; attempts={TOKEN_REMOVAL_ATTEMPTS}; control traffic summary enabled")
    if session.shutdown_control_debug:
        print("Shutdown control debug: enabled")
    print("Hamlib startup compatibility: get_lock_mode replies with value plus RPRT 0")
    if session.allow_real_tune:
        print("Real CI-V tune enabled: rigctl F sends radio frequency set commands")
        print("Real CI-V tune enabled: rigctl M sends radio mode set commands")
    else:
        print("Real CI-V tune disabled: rigctl F is cache-only; use --allow-real-tune to enable")
        print("Real CI-V tune disabled: rigctl M is cache-only; use --allow-real-tune to enable")
    print(f"Station rigctl PTT server listening on {rigctld_listen}:{rigctld_port}")
    print(f"Rigctl strict mode: {'enabled' if session.rigctl_strict else 'disabled'}")
    print(f"Station RX audio bridge: {'enabled' if station_rx_audio else 'disabled'}")
    if station_rx_audio:
        print(
            "Station RX audio rates: "
            f"radio={session.rx_sample_rate}Hz device={int(station_rx_device_sample_rate or session.rx_sample_rate)}Hz"
        )
    print(f"Station TX audio bridge: {'enabled' if station_tx_audio else 'disabled'}")
    if station_tx_audio:
        print(
            "Station TX audio rates: "
            f"device={int(station_tx_device_sample_rate or session.tx_sample_rate)}Hz radio={session.tx_sample_rate}Hz"
        )
        print("Station TX audio bridge stationkeeping: enabled inside TX audio loop")
    print("PTT authority: radio readback required for t and confirmed for T 1/T 0")
    print(
        "Station keepalives: "
        f"ping={STATION_CONTROL_PING_INTERVAL:.1f}s "
        f"idle={STATION_IDLE_CONTROL_INTERVAL:.1f}s "
        f"token={STATION_TOKEN_KEEPALIVE_INTERVAL:.1f}s "
        "quiet-during-CAT=yes"
    )
    print("Supported rigctl commands: PTT plus local startup/introspection compatibility shell.  Use Ctrl-C to exit station.")
    if rigctld_handshake:
        print("rigctld handshake banner enabled")
    if rigctld_debug_bytes:
        print("rigctld raw byte logging enabled")

    if station_rx_audio:
        preflight_sounddevice_device(station_rx_audio_device, "output", "station RX audio bridge")
    if station_tx_audio:
        preflight_sounddevice_device(station_tx_audio_device, "input", "station TX audio bridge")

    audio_threads: list[threading.Thread] = []
    if station_rx_audio:
        t_rx = threading.Thread(
            target=session.station_rx_audio_bridge_loop,
            kwargs={
                "stop": stop,
                "audio_device": station_rx_audio_device,
                "output_gain": station_rx_output_gain,
                "device_sample_rate": station_rx_device_sample_rate,
            },
            name="station-rx-audio",
            daemon=True,
        )
        audio_threads.append(t_rx)
        t_rx.start()
    if station_tx_audio:
        t_tx = threading.Thread(
            target=session.station_tx_audio_bridge_loop,
            kwargs={
                "stop": stop,
                "audio_device": station_tx_audio_device,
                "tx_local_input_gain": station_tx_input_gain,
                "device_sample_rate": station_tx_device_sample_rate,
            },
            name="station-tx-audio",
            daemon=True,
        )
        audio_threads.append(t_tx)
        t_tx.start()

    try:
        last_health = time.time()
        while not stop.is_set():
            # When RX audio bridge is enabled, that thread owns audio.recv().
            # Keep the control channel serviced here without stealing audio
            # packets from playback. TX audio only sends on the audio socket.
            try:
                session.station_keepalive_tick()
            except Exception as exc:
                with contextlib.suppress(Exception):
                    session.record_session_error("station:keepalive", exc)
                session.log("station keepalive failed:", repr(exc))
            if not station_rx_audio and session.audio is not None:
                with contextlib.suppress(Exception):
                    session.audio.service_until_empty()
            server.handle_request()
            for thread in audio_threads:
                if not thread.is_alive() and not stop.is_set():
                    session.log("station audio thread exited unexpectedly", thread.name)
                    with contextlib.suppress(Exception):
                        session.bump_station_counter("audio_thread_exit")
                        session.record_session_error("station:audio-thread", RuntimeError(f"{thread.name} exited"))
                    stop.set()
                    break
            if session.verbose and time.time() - last_health >= STATION_HEALTH_INTERVAL:
                _fold_endpoint_service_counts(session)
                session.log("station health:", session.station_health_summary())
                last_health = time.time()
    finally:
        stop.set()
        with contextlib.suppress(Exception):
            session.set_session_phase("station:shutdown")
        session.log("shutdown: closing TX audio gate and sending repeated PTT OFF safety commands before closing streams")
        session.tx_audio_gate_enabled = False
        with contextlib.suppress(Exception):
            session.send_ptt_off_recovery(radio_civ=radio_civ, reason="station-shutdown")
        session.ptt_state = False
        session.ptt_radio_state = False
        for thread in audio_threads:
            with contextlib.suppress(Exception):
                thread.join(timeout=1.0)
        server.server_close()
