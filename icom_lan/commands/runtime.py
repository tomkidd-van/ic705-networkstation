#!/usr/bin/env python3
"""Top-level command dispatch for the IC-705 Icom LAN CLI."""

from __future__ import annotations

import contextlib
import signal
import sys
import threading
import time
from argparse import Namespace
from typing import Optional, Protocol, TypeVar

from ..constants import SCRIPT_VERSION
from ..audio import inspect_wav_file
from ..errors import ProtocolError
from .local import list_audio_devices, rigctl_selftest


class SessionFactory(Protocol):
    def __call__(self, *args, **kwargs): ...


SessionT = TypeVar("SessionT")



def validate_radio_connection_args(args: Namespace) -> None:
    missing = [
        name
        for name in ("host", "user", "password")
        if not getattr(args, name, None)
    ]
    if missing:
        env_map = {"host": "ICOM_HOST", "user": "ICOM_USER", "password": "ICOM_PASSWORD"}
        details = ", ".join(f"--{name} or {env_map[name]}" for name in missing)
        raise SystemExit(f"Missing radio connection setting(s): {details}")


RX_ONLY_PROFILE_COMMANDS = {
    "probe",
    "audio-probe",
    "rx-audio",
    "rx-audio-raw",
    "rx-audio-buffered",
    "rx-stdout",
    "rx-file",
    "rx-aplay",
    "rx-record",
}


def command_requires_civ(command: str) -> bool:
    return command in ("ptt", "tx-wav", "tx-input", "station")


def command_allows_rx_only_profile(command: str) -> bool:
    return command in RX_ONLY_PROFILE_COMMANDS


def resolve_stream_profile(args: Namespace) -> dict[str, int]:
    """Return stream-request defaults after applying a named profile and lab overrides."""
    profile = getattr(args, "stream_profile", "conservative")
    if profile == "rx-only-minimal" and not command_allows_rx_only_profile(args.command):
        raise SystemExit(
            "--stream-profile rx-only-minimal is only valid for RX/probe commands; "
            "use --stream-profile conservative for PTT, TX or station commands."
        )

    values = {
        "stream_rx_enable": 1,
        "stream_tx_enable": 1,
        "stream_tx_buffer": 200,
        "stream_convert": 1,
    }
    if profile == "rx-only-minimal":
        # CAP-014 confirmed this receive-only profile produces normal RX audio
        # on the observed IC-705 path while still advertising valid codec bytes.
        values["stream_tx_enable"] = 0
        values["stream_tx_buffer"] = 0

    for attr, key in (
        ("stream_rx_enable", "stream_rx_enable"),
        ("stream_tx_enable", "stream_tx_enable"),
        ("stream_tx_buffer", "stream_tx_buffer"),
        ("stream_convert", "stream_convert"),
    ):
        override = getattr(args, attr, None)
        if override is not None:
            values[key] = int(override)
    return values


def create_session(args: Namespace, stop: threading.Event, session_cls: SessionFactory, *, enable_civ: bool):
    stream_profile_values = resolve_stream_profile(args)
    return session_cls(
        host=args.host,
        username=args.user,
        password=args.password,
        control_port=args.control_port,
        control_local_port=args.control_local_port,
        client_name=args.client_name,
        rx_sample_rate=args.stream_rx_sample_rate if args.stream_rx_sample_rate is not None else args.sample_rate,
        tx_sample_rate=args.stream_tx_sample_rate if args.stream_tx_sample_rate is not None else args.sample_rate,
        rx_codec=args.stream_rx_codec if args.stream_rx_codec is not None else args.rx_codec,
        tx_codec=args.stream_tx_codec if args.stream_tx_codec is not None else args.tx_codec,
        stream_rx_enable=stream_profile_values["stream_rx_enable"],
        stream_tx_enable=stream_profile_values["stream_tx_enable"],
        stream_tx_buffer=stream_profile_values["stream_tx_buffer"],
        stream_convert=stream_profile_values["stream_convert"],
        rx_gain=args.local_rx_playback_gain,
        rx_swap16=args.rx_swap16,
        rx_invert=args.rx_invert,
        rx_buffer_ms=args.local_rx_buffer_ms,
        rx_stats_interval=args.rx_stats_interval,
        tx_gain=0.0,
        tx_swap16=False,
        tx_invert=False,
        stream_retries=args.stream_retries,
        verbose=args.verbose,
        shutdown_control_debug=args.shutdown_control_debug,
        stop_event=stop,
        enable_civ=enable_civ,
    )


def run_local_command(args: Namespace, session_cls: SessionFactory) -> Optional[int]:
    if args.command == "list-audio-devices":
        return list_audio_devices()

    if args.command == "rigctl-selftest":
        return rigctl_selftest(args.host, args.port, args.rigctl_command, timeout=args.timeout)

    if args.command == "inspect-wav":
        info = inspect_wav_file(args.file, expect_rate=args.expect_rate, print_summary=True)
        return 0 if bool(info["valid_for_tx"]) else 2

    if args.command == "tx-wav" and getattr(args, "dry_run", False):
        try:
            info = inspect_wav_file(args.file, expect_rate=args.sample_rate, print_summary=True)
            if bool(info["valid_for_tx"]):
                print("TX WAV dry-run: validation passed; radio was not keyed and no audio was sent")
                return 0
            return 2
        except ProtocolError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    return None


def install_signal_handlers(stop: threading.Event) -> None:
    def handle_signal(_sig, _frame):
        if not stop.is_set():
            print("\nStopping; sending radio token removal and closing session...", file=sys.stderr)
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


def print_probe_summary(session, args: Namespace) -> None:
    print(f"Connected receive-only ({SCRIPT_VERSION})")
    print(f"  local_ip:          {session.local_ip}")
    print(f"  control_local:     {session.control.local_port}")
    print(f"  civ_local/remote:  {session.civ_local_port}/{session.civ_remote_port}  (negotiated but not opened/used)")
    print(f"  aud_local/remote:  {session.audio_local_port}/{session.audio_remote_port}")
    for i, r in enumerate(session.radios):
        print(f"  radio[{i}]:         {r.name} audio={r.audio} civ=0x{r.civ_addr:02X}")
    print("  commands:          RX subcommands send no CI-V/CAT/PTT/TX commands; TX/PTT subcommands send CI-V PTT only")
    print("  tx note:           TX codec advertised for stream request; TX used only by tx-* subcommands")
    print(f"  stream profile:    {getattr(args, 'stream_profile', 'conservative')}")
    print(
        "  stream request:    "
        f"rxen={getattr(session, 'stream_rx_enable', 1)} "
        f"txen={getattr(session, 'stream_tx_enable', 1)} "
        f"rx_codec=0x{session.rx_codec:02x} tx_codec=0x{session.tx_codec:02x} "
        f"rxsr={session.rx_sample_rate} txsr={session.tx_sample_rate} "
        f"txbuf={getattr(session, 'stream_tx_buffer', 200)} "
        f"convert={getattr(session, 'stream_convert', 1)}"
    )
    print(f"  local playback:    gain={args.local_rx_playback_gain} buffer_ms={args.local_rx_buffer_ms}")
    print("  shutdown:          Ctrl-C sends token removal 0x01 before closing control socket")
    print("  playback:          rx-audio uses raw PCM16LE direct playback by default")
    print("  pcm sinks:         rx-audio --audio-device, rx-file, rx-stdout and rx-aplay output stripped PCM16LE")
    print("  comparison:        use rx-audio-buffered only to compare old ring-buffer path")


def dispatch_connected_command(session, args: Namespace, stop: threading.Event) -> int:
    if args.command == "probe":
        print_probe_summary(session, args)
        return 0

    if args.command == "audio-probe":
        session.audio_probe_loop(stop, seconds=args.seconds)
        return 0
    if args.command in ("rx-audio", "rx-audio-raw"):
        session.rx_audio_raw_loop(stop, audio_device=getattr(args, "audio_device", None))
        return 0
    if args.command == "rx-audio-buffered":
        session.rx_audio_loop(stop)
        return 0
    if args.command == "rx-stdout":
        session.rx_stdout_loop(stop)
        return 0
    if args.command == "rx-file":
        session.rx_file_loop(stop, output=args.output)
        return 0
    if args.command == "rx-aplay":
        session.rx_aplay_loop(stop, device=args.device)
        return 0
    if args.command == "rx-record":
        session.rx_record(stop, seconds=args.seconds, prefix=args.prefix)
        return 0
    if args.command == "ptt":
        session.ptt_pulse(args.pulse, radio_civ=args.radio_civ)
        return 0
    if args.command == "station":
        session.run_station(
            stop,
            rigctld_listen=args.rigctld_listen,
            rigctld_port=args.rigctld_port,
            radio_civ=args.radio_civ,
            rigctld_handshake=args.rigctld_handshake,
            rigctld_debug_bytes=args.rigctld_debug_bytes,
            rigctld_strict=args.rigctld_strict,
            allow_real_tune=args.allow_real_tune,
            real_cat_cache_ttl=args.real_cat_cache_ttl,
            station_rx_audio=args.station_rx_audio,
            station_rx_audio_device=args.station_rx_audio_device,
            station_rx_output_gain=args.station_rx_output_gain,
            station_rx_device_sample_rate=args.station_rx_device_sample_rate,
            station_tx_audio=args.station_tx_audio,
            station_tx_audio_device=args.station_tx_audio_device,
            station_tx_input_gain=args.station_tx_input_gain,
            station_tx_device_sample_rate=args.station_tx_device_sample_rate,
        )
        return 0
    if args.command == "tx-wav":
        session.tx_wav(
            args.file,
            radio_civ=args.radio_civ,
            tx_local_input_gain=args.tx_local_input_gain,
            dry_run=args.dry_run,
        )
        return 0
    if args.command == "tx-input":
        session.tx_input(
            args.seconds,
            audio_device=args.audio_device,
            radio_civ=args.radio_civ,
            tx_local_input_gain=args.tx_local_input_gain,
        )
        return 0

    return 2


def record_top_level_error(session, exc: BaseException) -> None:
    with contextlib.suppress(Exception):
        session.record_session_error(getattr(session, "last_session_phase", "main"), exc)
        session.log("session observation:", session.session_observation_summary())


def cleanup_session(session) -> None:
    with contextlib.suppress(Exception):
        session.close_radio_session()
    time.sleep(0.20)
    with contextlib.suppress(Exception):
        session.close()


def run_main(args: Namespace, session_cls: SessionFactory) -> int:
    local_result = run_local_command(args, session_cls)
    if local_result is not None:
        return local_result

    validate_radio_connection_args(args)

    stop = threading.Event()
    install_signal_handlers(stop)

    session = create_session(
        args,
        stop,
        session_cls,
        enable_civ=command_requires_civ(args.command),
    )

    try:
        session.connect(request_streams=True)
        return dispatch_connected_command(session, args, stop)
    except KeyboardInterrupt:
        stop.set()
        print("\nInterrupted; sending token removal and releasing radio session...", file=sys.stderr)
        return 130
    except OSError as exc:
        if stop.is_set():
            return 130
        record_top_level_error(session, exc)
        raise
    except Exception as exc:
        record_top_level_error(session, exc)
        raise
    finally:
        cleanup_session(session)
