#!/usr/bin/env python3
"""
icom_lan.cli

Experimental, self-contained Python client for Icom LAN-capable radios.

Radio connection settings:
  The client does not embed radio host, username or password defaults.
  Supply them with --host/--user/--password or with ICOM_HOST, ICOM_USER
  and ICOM_PASSWORD environment variables.

Goals:
  - No external GUI client process, rigctld, hamlib or serial bridge required
  - Authenticate to the radio's Icom LAN control port
  - Request RX/TX audio and CI-V streams using the Icom LAN protocol
  - Receive raw PCM16LE audio
  - Send PTT ON/OFF over CI-V only when TX/PTT subcommands are used
  - Send TX audio from a WAV file or local input device

Protocol note:
  - The script sends the minimum Icom LAN session packets needed to get audio:
    discovery/ready, login, token confirmation, IC-705-compatible stream request
    and audio keepalives.
  - RX-only subcommands do not send CI-V/CAT/PTT/frequency/mode commands.
  - TX/PTT subcommands open the CI-V stream and send only CI-V PTT ON/OFF.

Tested status:
  - v103 is the current digital-safe receive-only test branch.
  - It uses observed IC-705-compatible stream-request fields because the IC-705 may reject
    a request with TX disabled/codec zero.
  - It still never opens CI-V and never sends CI-V/CAT/PTT/frequency/mode/TX commands.
  - No smoothing, no AGC, no noise reduction and no TX audio.
  - Local playback defaults are named explicitly:
      --local-rx-playback-gain
      --local-rx-buffer-ms
  - v11 adds best-effort Ctrl-C cleanup of the negotiated audio/session stream.
  - v12 proved raw PCM16 blocking playback avoids the callback/ring-buffer pops.
  - v13 made raw PCM16 playback the default rx-audio path.
  - v14 made the stripped PCM16LE stream the central abstraction.
  - v15 makes rx-audio able to target a named output device directly and keeps
    rx-file as the simple file/FIFO alternative.
  - v20 uses token-removal shutdown: stop local RX, send token removal
    sendToken(0x01) on the control channel while it is still open, then close
    local sockets.
  - The previous invented CONNINFO disconnect packet has been removed.
  - Ctrl-C during stream-retry loops stops promptly.
  - The old callback/ring-buffer path is retained as rx-audio-buffered for comparison only.

Dependencies:
  - Required for control/PTT only: Python 3.10+
  - Optional for audio modes: sounddevice, numpy
      python3 -m pip install sounddevice numpy

Examples:
  # Radio-facing examples assume these are exported first:
  export ICOM_HOST=<radio-ip>
  export ICOM_USER=<radio-user>
  export ICOM_PASSWORD='<radio-password>'

  # Probe/login/session using environment-provided radio credentials
  python3 -m icom_lan.cli -v probe

  # RX to default Mac/local output
  python3 -m icom_lan.cli -v rx-audio

  # RX to a Linux loopback playback device
  python3 -m icom_lan.cli -v rx-audio --audio-device "plughw:Loopback,0,0"

  # PTT pulse only
  python3 -m icom_lan.cli -v ptt --pulse 1.0

  # Station mode: one process owns radio session and exposes local rigctl PTT on 4532
  python3 -m icom_lan.cli -v station

  # From another terminal, first test the station listener without Hamlib:
  python3 -m icom_lan.cli rigctl-selftest --host 127.0.0.1 --command t

  # Then test PTT through rigctl:
  rigctl -m 2 -r 127.0.0.1:4532 T 1
  rigctl -m 2 -r 127.0.0.1:4532 T 0
  rigctl -m 2 -r 127.0.0.1:4532 t

  # TX from a WAV file. Start into a dummy load or low power.
  python3 -m icom_lan.cli -v tx-wav --file aprstest.wav --tx-local-input-gain 0.20

  # TX from an input device, e.g. Linux loopback capture. Start into a dummy load or low power.
  python3 -m icom_lan.cli -v tx-input --seconds 5 --audio-device "plughw:Loopback,1,0" --tx-local-input-gain 0.20

  # List local sounddevice/PortAudio devices
  python3 -m icom_lan.cli list-audio-devices

  # File/FIFO sink for stripped RX PCM16LE
  python3 -m icom_lan.cli -v rx-file --output ic705_rx.pcm

  # One-shot environment-variable form is also supported:
  ICOM_HOST=<radio-ip> ICOM_USER=<radio-user> ICOM_PASSWORD='<radio-password>' \
    python3 -m icom_lan.cli -v probe
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import ipaddress
import os
import queue
import random
import select
import signal
import subprocess
import socket
import socketserver
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Optional


from .constants import *  # packet sizes and conservative runtime defaults
from .audio import (
    describe_sounddevice_devices as audio_describe_sounddevice_devices,
    inspect_wav_file as audio_inspect_wav_file,
    preflight_sounddevice_device as audio_preflight_sounddevice_device,
    pcm16le_apply_gain as audio_pcm16le_apply_gain,
    resolve_sounddevice_selector as audio_resolve_sounddevice_selector,
    wav_pcm16le_chunks as audio_wav_pcm16le_chunks,
    write_wav_s16le as audio_write_wav_s16le,
)
from .civ import (
    CIV_MODE_CODES as CIV_MODE_CODES_TABLE,
    CIV_MODE_NAMES as CIV_MODE_NAMES_TABLE,
    build_civ_frame,
    decode_civ_bcd_frequency as civ_decode_bcd_frequency,
    decode_ptt_reply_body,
    handle_rigctl_real_cat as civ_handle_rigctl_real_cat,
    send_ptt_off_recovery as civ_send_ptt_off_recovery,
    ptt as civ_ptt,
    read_frequency_civ as civ_read_frequency_civ,
    read_mode_civ as civ_read_mode_civ,
    read_ptt_civ as civ_read_ptt_civ,
    set_frequency_civ as civ_set_frequency_civ,
    set_mode_civ as civ_set_mode_civ,
    set_ptt_and_confirm as civ_set_ptt_and_confirm,
    encode_civ_bcd_frequency as civ_encode_bcd_frequency,
    extract_civ_frames as civ_extract_frames,
    normalize_rigctl_mode_name as civ_normalize_mode_name,
)
from .errors import ProtocolError, StreamAllocationError
from .models import ConnInfoControl, RadioCapability
from .protocol import (
    UdpEndpoint,
    build_audio_packet,
    build_civ_data_packet,
    build_civ_open_close_packet,
    build_login_packet,
    build_stream_request_packet,
    build_token_packet,
    control_packet_summary,
    decode_stream_status_error,
    discover_local_ip,
    iter_audio_packet_chunks,
    parse_conninfo_control_packet,
    parse_radio_capabilities,
    parse_stream_status_packet,
    reserve_udp_ports,
    short_hex,
    summarize_control_packets as protocol_summarize_control_packets,
)
from .rigctl import (
    bool_value as rigctl_bool_value,
    dump_caps as rigctl_dump_caps_text,
    dump_state as rigctl_dump_state_text,
    format_response as rigctl_format_response,
    get_cached as rigctl_get_cached_value,
    int_string as rigctl_int_string,
    normalize_rigctl_command as rigctl_normalize_command,
    parse_number as rigctl_parse_number,
    query_list as rigctl_query_list_text,
    set_cached as rigctl_set_cached_value,
    vfo_info as rigctl_vfo_info_text,
)
from .rigctl.handlers import (
    handle_rigctl_cached_families as rigctl_handle_cached_families,
    handle_rigctl_line as rigctl_handle_line,
    handle_rigctl_misc_compat as rigctl_handle_misc_compat,
    handle_rigctl_startup_compat as rigctl_handle_startup_compat,
    handle_rigctl_station_state as rigctl_handle_station_state,
)
from .station import (
    build_station_health_summary,
    bump_counter as station_bump_counter,
    initialize_station_runtime_state,
    keepalive_tick as station_keepalive_tick_impl,
    record_rigctl_category,
    run_station as station_run_station_impl,
    station_rx_audio_bridge_loop as station_rx_audio_bridge_loop_impl,
    station_tx_audio_bridge_loop as station_tx_audio_bridge_loop_impl,
)
from .session import SessionLifecycleMixin


class IcomLanSession(SessionLifecycleMixin):
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        control_port: int = DEFAULT_CONTROL_PORT,
        control_local_port: int = 0,
        client_name: str = "pyicom",
        rx_sample_rate: int = DEFAULT_SAMPLE_RATE,
        tx_sample_rate: int = DEFAULT_SAMPLE_RATE,
        rx_codec: int = DEFAULT_RX_CODEC,
        tx_codec: int = DEFAULT_TX_CODEC,
        stream_rx_enable: int = 1,
        stream_tx_enable: int = 1,
        stream_tx_buffer: int = 200,
        stream_convert: int = 1,
        rx_gain: float = 1.0,
        rx_swap16: bool = False,
        rx_invert: bool = False,
        rx_buffer_ms: int = 250,
        rx_stats_interval: float = 2.0,
        tx_gain: float = 0.25,
        tx_swap16: bool = False,
        tx_invert: bool = False,
        stream_retries: int = 3,
        verbose: bool = False,
        shutdown_control_debug: bool = False,
        stop_event: Optional[threading.Event] = None,
        enable_civ: bool = False,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.control_port = control_port
        # Protocol-derivation lab option: bind the control UDP socket to a
        # known local source port so the packet-visible client/session id
        # formula can be confirmed.  The normal runtime value is 0, allowing
        # the OS to choose an ephemeral port.
        self.control_local_port = int(control_local_port or 0)
        self.client_name = (client_name[:8] + "-py")[:16]
        self.rx_sample_rate = rx_sample_rate
        self.tx_sample_rate = tx_sample_rate
        self.rx_codec = rx_codec
        self.tx_codec = tx_codec
        # Lab-only CAP-011 stream-request controls.  Defaults remain the
        # capture-confirmed accepted baseline; non-default values should be used
        # only for one-variable-at-a-time derivation captures.
        self.stream_rx_enable = int(stream_rx_enable)
        self.stream_tx_enable = int(stream_tx_enable)
        self.stream_tx_buffer = int(stream_tx_buffer)
        self.stream_convert = int(stream_convert)
        self.rx_gain = rx_gain
        self.rx_swap16 = rx_swap16
        self.rx_invert = rx_invert
        self.rx_buffer_ms = max(20, rx_buffer_ms)
        self.rx_stats_interval = max(0.25, rx_stats_interval)
        self.tx_gain = tx_gain
        self.tx_swap16 = tx_swap16
        self.tx_invert = tx_invert
        self.stream_retries = max(1, stream_retries)
        self.verbose = verbose
        self.shutdown_control_debug = shutdown_control_debug
        self.stop_event = stop_event
        self.enable_civ = enable_civ
        self.ptt_state = False
        self.ptt_radio_state: Optional[bool] = None
        self.local_ip = discover_local_ip(host, control_port)
        self.control = UdpEndpoint(self.local_ip, host, control_port, self.control_local_port, verbose)
        self.token_request = random.randint(1, 0xFFFF)
        self.token = 0
        self.radios: list[RadioCapability] = []
        self.selected_radio: Optional[RadioCapability] = None
        self.civ_local_port = 0
        self.audio_local_port = 0
        self.civ_remote_port = 0
        self.audio_remote_port = 0
        self.civ: Optional[UdpEndpoint] = None
        self.audio: Optional[UdpEndpoint] = None
        self.last_conninfo_control: Optional[ConnInfoControl] = None
        self.civ_lock = threading.RLock()
        self.civ_exchange_depth = 0
        self.rigctl_strict = False
        self.rigctl_category_counts: dict[str, int] = {}
        self.station_counters: dict[str, int] = {}
        self.station_started_at = time.time()
        self.station_last_control_ping = 0.0
        self.station_last_idle_control = 0.0
        self.station_last_token_keepalive = 0.0
        self.last_session_phase = "init"
        self.last_session_phase_at = time.time()
        self.last_session_error_phase: Optional[str] = None
        self.last_session_error_class: Optional[str] = None
        self.last_session_error_message: Optional[str] = None
        self.last_session_error_detail: Optional[str] = None
        self.last_session_error_at = 0.0
        self.session_error_count = 0

    def log(self, *parts: object) -> None:
        if self.verbose:
            print("[session]", *parts, file=sys.stderr)

    def close(self) -> None:
        for ep in (self.audio, self.civ, self.control):
            if ep is not None:
                with contextlib.suppress(Exception):
                    ep.close()




















    def civ_frame(self, command: bytes, radio_civ: Optional[int] = None, controller_addr: int = 0xE1) -> bytes:
        if radio_civ is None:
            radio_civ = self.selected_radio.civ_addr if self.selected_radio is not None else 0xA4
        return build_civ_frame(command, radio_civ=radio_civ, controller_addr=controller_addr)

    def civ_send_open(self, close: bool = False) -> None:
        # CI-V data stream open/close packet:
        #   len=0x16, data=0x01c0, sendseq big-endian at 0x13,
        #   magic=0x04 open or 0x00 close.
        if self.civ is None:
            return
        stream_seq = self.civ.send_seq_b & 0xFFFF
        packet = build_civ_open_close_packet(
            my_id=self.civ.my_id,
            remote_id=self.civ.remote_id,
            stream_seq=stream_seq,
            close=close,
        )
        self.civ.send_seq_b = (self.civ.send_seq_b + 1) & 0xFFFF
        self.log("CI-V data", "close" if close else "open", f"stream_seq={stream_seq}")
        self.civ.send_tracked(packet)

    def send_civ_payload(self, payload: bytes) -> None:
        # CI-V data payload packet:
        #   data_packet header is 0x15 bytes
        #   reply byte at 0x10 is 0xC1
        #   datalen is stored at 0x11
        #   sendseq is big-endian at 0x13
        if self.civ is None:
            raise ProtocolError("CI-V endpoint not open; use a TX/PTT subcommand")
        stream_seq = self.civ.send_seq_b & 0xFFFF
        packet = build_civ_data_packet(
            my_id=self.civ.my_id,
            remote_id=self.civ.remote_id,
            stream_seq=stream_seq,
            payload=payload,
        )
        self.log("sending CI-V payload", payload.hex(" "), f"stream_seq={stream_seq}")
        self.civ.send_seq_b = (self.civ.send_seq_b + 1) & 0xFFFF
        self.civ.send_tracked(packet)

    @staticmethod
    def decode_civ_bcd_frequency(payload: bytes) -> Optional[int]:
        """Decode Icom CI-V 5-byte little-endian BCD frequency payload."""
        return civ_decode_bcd_frequency(payload)

    @staticmethod
    def extract_civ_frames(packet: bytes) -> list[bytes]:
        """Extract CI-V frames from one Icom LAN CI-V data packet."""
        return civ_extract_frames(packet)

    def bump_station_counter(self, name: str, amount: int = 1) -> None:
        station_bump_counter(self.station_counters, name, amount)

    def rigctl_category_result(
        self,
        category: str,
        cmd: str,
        result: Optional[tuple[str, bool]],
        *,
        real: bool = False,
        strict_allowed: bool = True,
        extended: bool = False,
    ) -> Optional[tuple[str, bool]]:
        """Record/log rigctl command category and enforce optional strict mode."""
        return record_rigctl_category(
            self,
            category,
            cmd,
            result,
            real=real,
            strict_allowed=strict_allowed,
            extended=extended,
        )

    def station_health_summary(self) -> str:
        return build_station_health_summary(self)

    def flush_civ_pending(self, seconds: float = 0.03) -> None:
        if self.civ is None:
            return
        end_flush = time.time() + seconds
        while time.time() < end_flush:
            pkt = self.civ.recv(timeout=0.0)
            if pkt is None:
                break

    @contextlib.contextmanager
    def civ_exchange(self):
        """Mark an active CAT/CI-V exchange so station keepalives stay quiet.

        The radio tolerated proactive keepalives, but the first CAT read after
        idle sometimes landed on the edge of our timeout while the station loop
        was also sending token/idle/ping traffic.  Keepalives remain active
        between exchanges, but not during the tight send/wait window.
        """
        self.civ_exchange_depth += 1
        try:
            yield
        finally:
            self.civ_exchange_depth = max(0, self.civ_exchange_depth - 1)

    def wait_civ_frame(
        self,
        *,
        radio_civ: int,
        command: Optional[int] = None,
        ack: bool = False,
        timeout: float = CIV_READ_TIMEOUT,
    ) -> Optional[bytes]:
        """Wait for a radio-to-controller CI-V frame matching command or ACK.

        Local LAN echo frames are deliberately ignored by requiring frame[3] to
        match the radio CI-V address.  This helper centralizes the request/reply
        filtering so multiple CAT operations can share the same correctness
        rules while the caller holds self.civ_lock.
        """
        if self.civ is None:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            pkt = self.civ.recv(timeout=max(0.0, deadline - time.time()))
            if pkt is None:
                continue
            for frame in self.extract_civ_frames(pkt):
                self.log("CI-V frame rx", frame.hex(" "))
                if len(frame) < 6:
                    continue
                if frame[0:2] != b"\xfe\xfe" or frame[-1] != 0xFD:
                    continue
                if frame[3] != (radio_civ & 0xFF):
                    continue
                code = frame[4]
                if ack and code in (0xFB, 0xFA):
                    self.bump_station_counter("civ_ack_rx")
                    return frame
                if command is not None and code == (command & 0xFF):
                    self.bump_station_counter("civ_reply_rx")
                    return frame
        self.bump_station_counter("civ_timeout")
        return None

    def civ_query_frame(
        self,
        command_payload: bytes,
        *,
        radio_civ: Optional[int] = None,
        expect_command: Optional[int] = None,
        timeout: float = CIV_READ_TIMEOUT,
    ) -> Optional[bytes]:
        """Serialize one CI-V query and wait for its matching radio reply."""
        if self.civ is None:
            return None
        if radio_civ is None:
            radio_civ = self.selected_radio.civ_addr if self.selected_radio is not None else 0xA4
        if expect_command is None and command_payload:
            expect_command = command_payload[0]
        if hasattr(self, "station_keepalive_tick"):
            self.station_keepalive_tick()
        with self.civ_lock:
            with self.civ_exchange():
                self.flush_civ_pending()
                frame = self.civ_frame(command_payload, radio_civ=radio_civ)
                self.log("CI-V query", frame.hex(" "))
                self.send_civ_payload(frame)
                self.bump_station_counter("civ_query_tx")
                return self.wait_civ_frame(radio_civ=radio_civ, command=expect_command, timeout=timeout)

    def civ_command_ack(
        self,
        command_payload: bytes,
        *,
        radio_civ: Optional[int] = None,
        timeout: float = CIV_SET_ACK_TIMEOUT,
    ) -> bool:
        """Serialize one CI-V set command and wait for FB/FA acknowledgement."""
        if self.civ is None:
            return False
        if radio_civ is None:
            radio_civ = self.selected_radio.civ_addr if self.selected_radio is not None else 0xA4
        if hasattr(self, "station_keepalive_tick"):
            self.station_keepalive_tick()
        with self.civ_exchange():
            self.flush_civ_pending()
            frame = self.civ_frame(command_payload, radio_civ=radio_civ)
            self.log("CI-V command", frame.hex(" "))
            self.send_civ_payload(frame)
            self.bump_station_counter("civ_command_tx")
            ack_frame = self.wait_civ_frame(radio_civ=radio_civ, ack=True, timeout=timeout)
            if ack_frame is None:
                return False
            return ack_frame[4] == 0xFB

    def read_frequency_civ(self, radio_civ: Optional[int] = None, timeout: float = CIV_READ_TIMEOUT) -> Optional[int]:
        return civ_read_frequency_civ(self, radio_civ=radio_civ, timeout=timeout)

    @staticmethod
    def encode_civ_bcd_frequency(freq_hz: int) -> bytes:
        """Encode frequency in Hz as Icom CI-V 5-byte little-endian BCD."""
        return civ_encode_bcd_frequency(freq_hz)

    def set_frequency_civ(self, freq_hz: int, radio_civ: Optional[int] = None, timeout: float = CIV_SET_ACK_TIMEOUT) -> bool:
        return civ_set_frequency_civ(self, freq_hz, radio_civ=radio_civ, timeout=timeout)

    CIV_MODE_NAMES = CIV_MODE_NAMES_TABLE
    CIV_MODE_CODES = CIV_MODE_CODES_TABLE

    @staticmethod
    def normalize_rigctl_mode_name(mode: str) -> str:
        return civ_normalize_mode_name(mode)

    def set_mode_civ(self, mode: str, width: int = 0, radio_civ: Optional[int] = None, timeout: float = CIV_SET_ACK_TIMEOUT) -> bool:
        return civ_set_mode_civ(self, mode, width=width, radio_civ=radio_civ, timeout=timeout)

    def read_mode_civ(self, radio_civ: Optional[int] = None, timeout: float = CIV_READ_TIMEOUT) -> Optional[tuple[str, int]]:
        return civ_read_mode_civ(self, radio_civ=radio_civ, timeout=timeout)

    def read_ptt_civ(self, radio_civ: Optional[int] = None, timeout: float = CIV_PTT_READ_TIMEOUT) -> Optional[bool]:
        return civ_read_ptt_civ(self, radio_civ=radio_civ, timeout=timeout)

    def ptt(self, enabled: bool, radio_civ: Optional[int] = None) -> None:
        civ_ptt(self, enabled, radio_civ=radio_civ)

    def set_ptt_and_confirm(
        self,
        enabled: bool,
        *,
        radio_civ: Optional[int] = None,
        timeout: float = CIV_PTT_READ_TIMEOUT,
    ) -> bool:
        return civ_set_ptt_and_confirm(self, enabled, radio_civ=radio_civ, timeout=timeout)

    def send_ptt_off_recovery(
        self,
        *,
        radio_civ: Optional[int] = None,
        confirm: bool = True,
        timeout: float = CIV_PTT_READ_TIMEOUT,
        reason: str = "unspecified",
    ) -> Optional[bool]:
        return civ_send_ptt_off_recovery(
            self,
            radio_civ=radio_civ,
            confirm=confirm,
            timeout=timeout,
            reason=reason,
        )

    def civ_prime_for_station(self, radio_civ: Optional[int] = None) -> None:
        """Prime the just-opened CI-V data stream with safe PTT OFF."""
        if self.civ is None:
            return
        self.set_session_phase("station:prime-civ")
        time.sleep(0.20)
        try:
            self.set_ptt_and_confirm(False, radio_civ=radio_civ)
            time.sleep(0.10)
            self.service_streams_once()
            self.set_session_phase("station:prime-civ-complete")
            self.log("station CI-V prime complete")
        except Exception as exc:
            self.record_session_error("station:prime-civ", exc)
            self.log("station CI-V prime failed:", repr(exc))

    def ptt_pulse(self, seconds: float, radio_civ: Optional[int] = None) -> None:
        if not self.set_ptt_and_confirm(True, radio_civ=radio_civ):
            raise ProtocolError("PTT ON was not confirmed by radio")
        try:
            time.sleep(max(0.0, seconds))
        finally:
            if not self.set_ptt_and_confirm(False, radio_civ=radio_civ):
                raise ProtocolError("PTT OFF was not confirmed by radio")

    @staticmethod
    def pcm16le_apply_gain(payload: bytes, gain: float) -> bytes:
        return audio_pcm16le_apply_gain(payload, gain)

    def station_keepalive_tick(self, *, force: bool = False) -> None:
        """Send proactive keepalives for long-running station mode."""
        station_keepalive_tick_impl(self, force=force)

    def tx_pcm16le_stream(self, chunks, *, radio_civ: Optional[int], tx_local_input_gain: float = 1.0, ptt_delay: float = 0.15) -> None:
        if self.civ is None:
            raise ProtocolError("CI-V endpoint not open; TX requires enable_civ=True")
        if self.audio is None:
            raise ProtocolError("Audio endpoint not open")
        self.collect_endpoint_service_counts()
        with contextlib.suppress(Exception):
            self.service_control_stationkeeping()
        self.ptt(True, radio_civ=radio_civ)
        last_ping = 0.0
        try:
            time.sleep(max(0.0, ptt_delay))
            with contextlib.suppress(Exception):
                self.service_control_stationkeeping()
            for payload in chunks:
                if not payload:
                    continue
                if len(payload) % 2:
                    payload = payload[:-1]
                if tx_local_input_gain != 1.0:
                    payload = self.pcm16le_apply_gain(payload, tx_local_input_gain)
                self.send_audio_payload(payload)
                with contextlib.suppress(Exception):
                    self.service_control_stationkeeping()
                now = time.time()
                if now - last_ping >= 0.1:
                    with contextlib.suppress(Exception):
                        self.audio.send_ping_request()
                    last_ping = now
        finally:
            time.sleep(0.05)
            with contextlib.suppress(Exception):
                self.service_control_stationkeeping()
            self.ptt(False, radio_civ=radio_civ)
            with contextlib.suppress(Exception):
                self.service_control_stationkeeping()
            counts = self.collect_endpoint_service_counts()
            if self.verbose and counts:
                self.log("TX control stationkeeping summary:", self.format_service_counts(counts))

    def inspect_wav_file(self, wav_path: str, *, expect_rate: Optional[int] = None, print_summary: bool = True) -> dict[str, object]:
        return audio_inspect_wav_file(
            wav_path,
            expect_rate=int(expect_rate if expect_rate is not None else self.tx_sample_rate),
            print_summary=print_summary,
        )

    def wav_pcm16le_chunks(self, wav_path: str, block_frames: int = 320, *, validate: bool = True):
        return audio_wav_pcm16le_chunks(
            wav_path,
            sample_rate=self.tx_sample_rate,
            block_frames=block_frames,
            validate=validate,
            verbose=self.verbose,
        )

    def tx_wav(
        self,
        wav_path: str,
        *,
        radio_civ: Optional[int],
        tx_local_input_gain: float = 1.0,
        dry_run: bool = False,
    ) -> None:
        print(
            f"TX WAV: file={wav_path!r} tx_sample_rate={self.tx_sample_rate} "
            f"tx_local_input_gain={tx_local_input_gain} dry_run={dry_run}"
        )
        info = self.inspect_wav_file(wav_path, expect_rate=self.tx_sample_rate, print_summary=True)
        if not bool(info["valid_for_tx"]):
            issues = "; ".join(str(x) for x in info["issues"])
            raise ProtocolError(f"WAV is not valid for direct tx-wav: {issues}")
        if dry_run:
            print("TX WAV dry-run: validation passed; radio was not keyed and no audio was sent")
            return
        self.tx_pcm16le_stream(
            self.wav_pcm16le_chunks(wav_path, validate=False),
            radio_civ=radio_civ,
            tx_local_input_gain=tx_local_input_gain,
        )

    def tx_input(self, seconds: float, *, audio_device: Optional[str], radio_civ: Optional[int], tx_local_input_gain: float = 1.0) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise SystemExit("tx-input requires: python3 -m pip install sounddevice") from exc

        block_frames = int(self.tx_sample_rate * TX_AUDIO_BLOCK_MS)
        device_label = audio_device if audio_device is not None else "default"
        print(
            f"TX input: device={device_label!r} seconds={seconds} "
            f"format=s16le channels=1 sample_rate={self.tx_sample_rate} "
            f"tx_local_input_gain={tx_local_input_gain}"
        )

        def chunks():
            deadline = time.time() + seconds
            with sd.RawInputStream(
                samplerate=self.tx_sample_rate,
                channels=1,
                dtype="int16",
                blocksize=block_frames,
                device=audio_device,
            ) as stream:
                while time.time() < deadline:
                    data, overflowed = stream.read(block_frames)
                    if overflowed and self.verbose:
                        print("[tx-input] overflow", file=sys.stderr)
                    yield bytes(data)

        self.tx_pcm16le_stream(chunks(), radio_civ=radio_civ, tx_local_input_gain=tx_local_input_gain)

    def _decode_rx_payload_to_float32(self, payload: bytes):
        import numpy as np

        # Keep even byte count for int16 decode.
        if len(payload) % 2:
            payload = payload[:-1]
        if self.rx_swap16:
            arr = np.frombuffer(payload, dtype=">i2").astype(np.float32)
        else:
            arr = np.frombuffer(payload, dtype="<i2").astype(np.float32)
        if self.rx_invert:
            arr = -arr
        arr = (arr / 32768.0) * float(self.rx_gain)
        return np.clip(arr, -1.0, 1.0)

    def rx_audio_loop(self, stop: threading.Event) -> None:
        if self.audio is None:
            raise ProtocolError("Audio endpoint not open")
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError as exc:
            raise SystemExit("Audio mode requires: python3 -m pip install sounddevice numpy") from exc

        # RX audio packets carry a 0x18-byte Icom LAN audio header followed by the payload, which is then handed to
        # the selected audio backend. We keep the same extraction point, but feed
        # playback with a continuous jitter/ring buffer instead of whole UDP chunks.
        buffer_samples = max(
            int(self.rx_sample_rate * self.rx_buffer_ms / 1000),
            int(self.rx_sample_rate * 0.05),
        )
        prebuffer_samples = max(
            int(self.rx_sample_rate * min(self.rx_buffer_ms, 150) / 1000),
            int(self.rx_sample_rate * 0.04),
        )

        ring = np.zeros(buffer_samples, dtype=np.float32)
        lock = threading.Lock()
        write_pos = 0
        read_pos = 0
        fill = 0

        stats = {
            "packets": 0,
            "samples": 0,
            "raw_peak": 0,
            "clip_samples": 0,
            "underruns": 0,
            "overruns": 0,
            "last_print": time.time(),
            "started_playback": False,
            "last_ping": 0.0,
        }

        def ring_write(samples) -> None:
            nonlocal write_pos, read_pos, fill
            n = int(len(samples))
            if n <= 0:
                return
            with lock:
                if n >= buffer_samples:
                    samples = samples[-buffer_samples:]
                    n = int(len(samples))
                    read_pos = 0
                    write_pos = 0
                    fill = 0

                free = buffer_samples - fill
                if n > free:
                    drop = n - free
                    read_pos = (read_pos + drop) % buffer_samples
                    fill -= drop
                    stats["overruns"] += 1

                first = min(n, buffer_samples - write_pos)
                ring[write_pos:write_pos + first] = samples[:first]
                second = n - first
                if second:
                    ring[:second] = samples[first:first + second]
                write_pos = (write_pos + n) % buffer_samples
                fill += n

        def ring_read(n: int):
            nonlocal read_pos, fill
            out = np.zeros(n, dtype=np.float32)
            with lock:
                available = min(n, fill)
                if available:
                    first = min(available, buffer_samples - read_pos)
                    out[:first] = ring[read_pos:read_pos + first]
                    second = available - first
                    if second:
                        out[first:first + second] = ring[:second]
                    read_pos = (read_pos + available) % buffer_samples
                    fill -= available
                if available < n:
                    stats["underruns"] += 1
            return out

        def output_callback(outdata, frames, _time_info, status):
            if status and self.verbose:
                print("[audio-out]", status, file=sys.stderr)

            with lock:
                current_fill = fill
            if not stats["started_playback"]:
                if current_fill < prebuffer_samples:
                    outdata.fill(0)
                    return
                stats["started_playback"] = True

            samples = ring_read(frames)
            outdata[:] = samples.reshape(-1, 1)

        def reader():
            while not stop.is_set():
                now = time.time()
                if now - float(stats["last_ping"]) >= 0.1:
                    with contextlib.suppress(Exception):
                        self.audio.send_ping_request()
                    stats["last_ping"] = now

                data = self.audio.recv(0.05)
                if not data or len(data) <= AUDIO_SIZE:
                    continue

                payload = data[AUDIO_SIZE:]
                if len(payload) < 2:
                    continue

                even_payload = payload if len(payload) % 2 == 0 else payload[:-1]
                raw = np.frombuffer(even_payload, dtype=">i2" if self.rx_swap16 else "<i2")
                if len(raw):
                    abs_raw = np.abs(raw.astype(np.int32))
                    stats["raw_peak"] = max(int(stats["raw_peak"]), int(abs_raw.max()))
                    stats["samples"] += int(len(raw))

                samples = self._decode_rx_payload_to_float32(payload)
                stats["clip_samples"] += int(np.count_nonzero(np.abs(samples) >= 0.999))
                stats["packets"] += 1
                ring_write(samples)

                if self.verbose and time.time() - float(stats["last_print"]) >= self.rx_stats_interval:
                    sample_count = max(1, int(stats["samples"]))
                    clip_pct = 100.0 * int(stats["clip_samples"]) / sample_count
                    with lock:
                        fill_now = fill
                    print(
                        f"[audio-rx] packets={stats['packets']} raw_peak={stats['raw_peak']} "
                        f"clip={clip_pct:.2f}% fill={fill_now}/{buffer_samples} "
                        f"underruns={stats['underruns']} overruns={stats['overruns']} "
                        f"local_gain={self.rx_gain} sr={self.rx_sample_rate} "
                        f"swap16={self.rx_swap16} invert={self.rx_invert}",
                        file=sys.stderr,
                    )
                    stats["last_print"] = time.time()

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        print(
            f"RX audio: sample_rate={self.rx_sample_rate} "
            f"local_playback_gain={self.rx_gain} local_buffer_ms={self.rx_buffer_ms} "
            f"swap16={self.rx_swap16} invert={self.rx_invert}"
        )
        with sd.OutputStream(
            samplerate=self.rx_sample_rate,
            channels=1,
            dtype="float32",
            blocksize=0,
            latency="high",
            callback=output_callback,
        ):
            while not stop.is_set():
                time.sleep(0.1)

    def audio_probe_loop(self, stop: threading.Event, seconds: float = 10.0) -> None:
        """Receive audio UDP packets and print packet sizes/header bytes without playing audio."""
        if self.audio is None:
            raise ProtocolError("Audio endpoint not open")
        deadline = time.time() + seconds
        count = 0
        total_payload = 0
        first_packets: list[bytes] = []
        print(f"Audio probe listening on {self.local_ip}:{self.audio_local_port} from {self.host}:{self.audio_remote_port}")
        while not stop.is_set() and time.time() < deadline:
            data = self.audio.recv(0.5)
            if not data:
                continue
            if len(data) <= AUDIO_SIZE:
                continue
            count += 1
            payload = data[AUDIO_SIZE:]
            total_payload += len(payload)
            if len(first_packets) < 8:
                first_packets.append(data[:min(len(data), 48)])
                print(
                    f"packet {count}: len={len(data)} payload={len(payload)} "
                    f"header={data[:AUDIO_SIZE].hex(' ')}"
                )
        print(f"Audio probe complete: packets={count} payload_bytes={total_payload}")
        if count == 0:
            print("No audio UDP payload packets arrived.  This points to stream allocation, firewall or radio-side audio forwarding.")
        else:
            print("Audio UDP payload packets are arriving.  If rx-audio is distorted, try --local-rx-playback-gain 0.25 first, then sweep --sample-rate.")

    def send_audio_payload(self, payload: bytes) -> None:
        if self.audio is None:
            raise ProtocolError("Audio endpoint not open")

        # TX audio payloads are split into <=1364 byte chunks. The stream header sets
        # ident=0x9781 when the payload is exactly 0xA0 bytes, otherwise 0x0080.
        # It writes TX sequence and payload length as big-endian fields.
        for partial in iter_audio_packet_chunks(payload):
            packet = build_audio_packet(
                my_id=self.audio.my_id,
                remote_id=self.audio.remote_id,
                stream_seq=self.audio.send_seq_b,
                payload=partial,
            )
            self.audio.send_seq_b = (self.audio.send_seq_b + 1) & 0xFFFF
            self.audio.send_tracked(packet)

    def _float_to_tx_pcm(self, samples) -> bytes:
        import numpy as np

        data = samples.astype(np.float32)
        if self.tx_invert:
            data = -data
        data = np.clip(data * float(self.tx_gain), -1.0, 1.0)
        pcm = (data * 32767.0).astype(">i2" if self.tx_swap16 else "<i2")
        return pcm.tobytes()

    def tx_audio_for_seconds(self, seconds: float, radio_civ: int) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise SystemExit("Audio mode requires: python3 -m pip install sounddevice numpy") from exc

        block_frames = int(self.tx_sample_rate * TX_AUDIO_BLOCK_MS)
        deadline = time.time() + seconds
        print(
            f"TX mic audio: sample_rate={self.tx_sample_rate} gain={self.tx_gain} "
            f"swap16={self.tx_swap16} invert={self.tx_invert} seconds={seconds}"
        )
        self.ptt(True, radio_civ)
        time.sleep(0.15)
        try:
            with sd.InputStream(samplerate=self.tx_sample_rate, channels=1, dtype="float32", blocksize=block_frames) as stream:
                while time.time() < deadline:
                    frames, _overflowed = stream.read(block_frames)
                    pcm = self._float_to_tx_pcm(frames[:, 0])
                    self.send_audio_payload(pcm)
                    self.service_streams_once()
                    with contextlib.suppress(Exception):
                        self.audio.send_ping_request()
        finally:
            time.sleep(0.1)
            self.ptt(False, radio_civ)

    def tx_tone_for_seconds(self, seconds: float, radio_civ: int, hz: float = 1000.0) -> None:
        try:
            import numpy as np
        except ImportError as exc:
            raise SystemExit("TX tone mode requires: python3 -m pip install numpy") from exc

        block_frames = int(self.tx_sample_rate * TX_AUDIO_BLOCK_MS)
        phase = 0.0
        step = 2.0 * np.pi * float(hz) / float(self.tx_sample_rate)
        deadline = time.time() + seconds
        print(
            f"TX tone: hz={hz} sample_rate={self.tx_sample_rate} gain={self.tx_gain} "
            f"swap16={self.tx_swap16} invert={self.tx_invert} seconds={seconds}"
        )
        self.ptt(True, radio_civ)
        time.sleep(0.15)
        try:
            while time.time() < deadline:
                idx = np.arange(block_frames, dtype=np.float32)
                samples = np.sin(phase + step * idx)
                phase = float((phase + step * block_frames) % (2.0 * np.pi))
                self.send_audio_payload(self._float_to_tx_pcm(samples))
                self.service_streams_once()
                with contextlib.suppress(Exception):
                    self.audio.send_ping_request()
                time.sleep(block_frames / float(self.tx_sample_rate))
        finally:
            time.sleep(0.1)
            self.ptt(False, radio_civ)

    def iter_rx_pcm16le_payloads(self, stop: threading.Event):
        """Yield stripped PCM16LE payload bytes from Icom LAN audio packets.

        Central receive abstraction:
          UDP datagram -> strip 0x18-byte Icom LAN audio header -> yield PCM16LE bytes

        This method intentionally does not scale, smooth, resample or reinterpret
        the payload.  The stream request asks the radio for codec 0x04, which is
        mono signed PCM16LE in this workflow.
        """
        if self.audio is None:
            raise ProtocolError("Audio endpoint not open")

        last_ping = 0.0
        while not stop.is_set():
            now = time.time()
            if now - last_ping >= 0.1:
                with contextlib.suppress(Exception):
                    self.audio.send_ping_request()
                last_ping = now

            try:
                data = self.audio.recv(0.05)
            except OSError:
                if stop.is_set():
                    break
                raise
            if not data or len(data) <= AUDIO_SIZE:
                continue

            payload = data[AUDIO_SIZE:]
            # Keep PCM16 frame alignment.  This should normally be even.
            if len(payload) % 2:
                payload = payload[:-1]
            if payload:
                yield payload

    def rx_pcm_sink_loop(self, stop: threading.Event, sink, *, close_sink: bool = False, stats_label: str = "pcm") -> None:
        """Write stripped PCM16LE payloads to any binary file-like sink."""
        packets = 0
        byte_count = 0
        last_print = time.time()

        try:
            for payload in self.iter_rx_pcm16le_payloads(stop):
                sink.write(payload)
                # Some sinks, including pipes/FIFOs, may buffer.  Flushing each packet
                # favours low latency and simple behaviour over maximum throughput.
                with contextlib.suppress(Exception):
                    sink.flush()
                packets += 1
                byte_count += len(payload)

                if self.verbose and time.time() - last_print >= self.rx_stats_interval:
                    print(
                        f"[{stats_label}] packets={packets} bytes={byte_count} "
                        f"sample_rate={self.rx_sample_rate} format=s16le channels=1",
                        file=sys.stderr,
                    )
                    last_print = time.time()
        finally:
            if close_sink:
                with contextlib.suppress(Exception):
                    sink.close()

    def rx_audio_raw_loop(self, stop: threading.Event, audio_device: Optional[str] = None) -> None:
        """Play RX audio by writing raw PCM16LE payload bytes directly.

        This is the least processed live-monitor path:
          UDP audio packet -> strip 0x18-byte Icom header -> write payload bytes
        No float conversion, no gain, no ring buffer, no smoothing.

        It is intended to determine whether pops/clicks are introduced by the
        callback/ring-buffer playback path or are already present in the received
        payload/transport.
        """
        if self.audio is None:
            raise ProtocolError("Audio endpoint not open")
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise SystemExit("Audio mode requires: python3 -m pip install sounddevice numpy") from exc

        stats = {
            "packets": 0,
            "bytes": 0,
            "seq_gaps_be": 0,
            "short_packets": 0,
            "odd_payloads": 0,
            "last_seq": None,
            "last_arrival": None,
            "max_gap_ms": 0.0,
            "late_packets": 0,
            "last_print": time.time(),
            "last_ping": 0.0,
        }

        rx_output_gain = float(self.rx_gain)
        resolved_audio_device = resolve_sounddevice_selector(audio_device)
        device_label = resolved_audio_device if resolved_audio_device is not None else "default"
        print(
            f"RX raw playback: sample_rate={self.rx_sample_rate} dtype=int16 "
            f"device={device_label!r} local_playback_gain={rx_output_gain}; "
            "writing PCM16LE payload bytes with optional gain"
        )

        # Give CoreAudio a conservative latency, but do not maintain our own
        # sample ring buffer in this path.
        with sd.RawOutputStream(
            samplerate=self.rx_sample_rate,
            channels=1,
            dtype="int16",
            latency="high",
            blocksize=0,
            device=resolved_audio_device,
        ) as stream:
            while not stop.is_set():
                now = time.time()
                if now - float(stats["last_ping"]) >= 0.1:
                    with contextlib.suppress(Exception):
                        self.audio.send_ping_request()
                    stats["last_ping"] = now

                try:
                    data = self.audio.recv(0.05)
                except OSError:
                    if stop.is_set():
                        break
                    raise
                if not data:
                    continue
                if len(data) <= AUDIO_SIZE:
                    stats["short_packets"] += 1
                    continue

                arrival = time.time()
                if stats["last_arrival"] is not None:
                    gap_ms = (arrival - float(stats["last_arrival"])) * 1000.0
                    stats["max_gap_ms"] = max(float(stats["max_gap_ms"]), gap_ms)
                    # Expected cadence is about 20 ms.  Flag big scheduling/network gaps.
                    if gap_ms > 40.0:
                        stats["late_packets"] += 1
                stats["last_arrival"] = arrival

                # CAP-004 evidence pass: RX audio packet sequence is big-endian at
                # offsets 0x12:0x14.  Earlier reports interpreted this field as
                # little-endian and falsely reported gaps because 00 01 became 256.
                seq = int.from_bytes(data[0x12:0x14], "big", signed=False)
                last_seq = stats["last_seq"]
                if last_seq is not None and ((int(last_seq) + 1) & 0xFFFF) != seq:
                    stats["seq_gaps_be"] += 1
                stats["last_seq"] = seq

                payload = data[AUDIO_SIZE:]
                if len(payload) % 2:
                    stats["odd_payloads"] += 1
                    payload = payload[:-1]
                if not payload:
                    continue
                if rx_output_gain != 1.0:
                    payload = self.pcm16le_apply_gain(payload, rx_output_gain)

                stream.write(payload)

                stats["packets"] += 1
                stats["bytes"] += len(payload)

                if self.verbose and time.time() - float(stats["last_print"]) >= self.rx_stats_interval:
                    print(
                        f"[audio-raw] packets={stats['packets']} bytes={stats['bytes']} "
                        f"seq_gaps_be={stats['seq_gaps_be']} late_packets={stats['late_packets']} "
                        f"max_gap_ms={stats['max_gap_ms']:.1f} short={stats['short_packets']} "
                        f"odd={stats['odd_payloads']}",
                        file=sys.stderr,
                    )
                    stats["last_print"] = time.time()
                    stats["max_gap_ms"] = 0.0

    def rx_stdout_loop(self, stop: threading.Event) -> None:
        """Write stripped PCM16LE mono 16 kHz audio to stdout."""
        self.rx_pcm_sink_loop(stop, sys.stdout.buffer, stats_label="stdout")

    def rx_file_loop(self, stop: threading.Event, output: str) -> None:
        """Write stripped PCM16LE to a regular file or FIFO path."""
        print(
            f"RX file/FIFO sink: output={output} format=s16le channels=1 "
            f"sample_rate={self.rx_sample_rate}",
            file=sys.stderr,
        )
        with open(output, "wb", buffering=0) as f:
            self.rx_pcm_sink_loop(stop, f, stats_label="file")

    def rx_aplay_loop(self, stop: threading.Event, device: Optional[str] = None) -> None:
        """Launch aplay and feed it stripped PCM16LE audio on stdin."""
        cmd = ["aplay", "-q", "-f", "S16_LE", "-c", "1", "-r", str(self.rx_sample_rate)]
        if device:
            cmd.extend(["-D", device])

        print("Launching:", " ".join(cmd), file=sys.stderr)
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        assert proc.stdin is not None

        try:
            self.rx_pcm_sink_loop(stop, proc.stdin, close_sink=True, stats_label="aplay")
        finally:
            with contextlib.suppress(Exception):
                proc.terminate()
            with contextlib.suppress(Exception):
                proc.wait(timeout=2)

    @staticmethod
    def _write_wav_s16le(path: str, pcm_s16le: bytes, sample_rate: int) -> None:
        audio_write_wav_s16le(path, pcm_s16le, sample_rate)

    def rx_record(self, stop: threading.Event, seconds: float, prefix: str) -> None:
        """Record received audio payloads without smoothing or sample processing.

        The radio is asked for RX codec 0x04 by default, which is PCM16 little-endian.
        This method writes:
          <prefix>_payload.raw  exact concatenated payload bytes after 0x18-byte header
          <prefix>_s16le.wav    the same bytes wrapped as mono 16-bit WAV
          <prefix>_report.txt   timing/header/sequence diagnostics
        """
        if self.audio is None:
            raise ProtocolError("Audio endpoint not open")

        deadline = time.time() + seconds
        payload_chunks: list[bytes] = []
        length_hist: dict[int, int] = {}
        ident_hist: dict[int, int] = {}
        seq_gaps = 0
        seq_gaps_le_interpretation = 0
        packets = 0
        last_seq: Optional[int] = None
        last_seq_le: Optional[int] = None
        first_headers: list[str] = []
        last_ping = 0.0

        print(f"RX record: {seconds:.1f}s, sample_rate={self.rx_sample_rate}, prefix={prefix}")
        while not stop.is_set() and time.time() < deadline:
            now = time.time()
            if now - last_ping >= 0.1:
                with contextlib.suppress(Exception):
                    self.audio.send_ping_request()
                last_ping = now

            try:
                data = self.audio.recv(0.05)
            except OSError:
                if stop.is_set():
                    break
                raise
            if not data or len(data) <= AUDIO_SIZE:
                continue

            payload = data[AUDIO_SIZE:]
            packets += 1
            payload_chunks.append(payload)
            length_hist[len(payload)] = length_hist.get(len(payload), 0) + 1

            ident = int.from_bytes(data[0x10:0x12], "little", signed=False)
            ident_hist[ident] = ident_hist.get(ident, 0) + 1

            # CAP-004 evidence pass: RX audio packets carry the stream sequence
            # at offsets 0x12:0x14 in big-endian order.  The earlier
            # little-endian interpretation produced false gaps such as
            # 0, 256, 512 for on-wire bytes 00 00, 00 01, 00 02.
            # Keep the little-endian value in the report only as a diagnostic
            # cross-check for old captures and tooling.
            seq_be = int.from_bytes(data[0x12:0x14], "big", signed=False)
            seq_le = int.from_bytes(data[0x12:0x14], "little", signed=False)
            if last_seq is not None and ((last_seq + 1) & 0xFFFF) != seq_be:
                seq_gaps += 1
            if last_seq_le is not None and ((last_seq_le + 1) & 0xFFFF) != seq_le:
                seq_gaps_le_interpretation += 1
            last_seq = seq_be
            last_seq_le = seq_le

            if len(first_headers) < 12:
                datalen_be = int.from_bytes(data[0x16:0x18], "big", signed=False)
                datalen_le = int.from_bytes(data[0x16:0x18], "little", signed=False)
                first_headers.append(
                    f"len={len(data)} payload={len(payload)} ident=0x{ident:04x} "
                    f"seq_be={seq_be} seq_le={seq_le} datalen_be={datalen_be} datalen_le={datalen_le} "
                    f"header={data[:AUDIO_SIZE].hex(' ')}"
                )

        raw_payload = b"".join(payload_chunks)
        raw_path = f"{prefix}_payload.raw"
        wav_path = f"{prefix}_s16le_{self.rx_sample_rate}hz.wav"
        report_path = f"{prefix}_report.txt"

        Path(raw_path).write_bytes(raw_payload)
        self._write_wav_s16le(wav_path, raw_payload if len(raw_payload) % 2 == 0 else raw_payload[:-1], self.rx_sample_rate)

        report_lines = [
            "icom_lan_rx_record_v90 receive-only report",
            f"packets={packets}",
            f"payload_bytes={len(raw_payload)}",
            f"sample_rate={self.rx_sample_rate}",
            f"seq_gaps_be={seq_gaps}",
            f"seq_gaps_le_interpretation={seq_gaps_le_interpretation}",
            "sequence_note=RX audio sequence is interpreted as big-endian at header offsets 0x12:0x14",
            f"payload_length_hist={dict(sorted(length_hist.items()))}",
            f"ident_hist={ {hex(k): v for k, v in sorted(ident_hist.items())} }",
            "first_headers:",
            *[f"  {line}" for line in first_headers],
            "",
            "No CI-V/CAT/PTT/TX commands are sent by this receive-only script.",
            "Audio payload is written directly after stripping the 0x18-byte Icom LAN audio header.",
        ]
        Path(report_path).write_text("\n".join(report_lines) + "\n")

        print(f"Wrote {raw_path}")
        print(f"Wrote {wav_path}")
        print(f"Wrote {report_path}")
        print(f"packets={packets} payload_bytes={len(raw_payload)} seq_gaps_be={seq_gaps}")


    def rigctl_response(
        self,
        ok: bool = True,
        value: Optional[str] = None,
        *,
        extended: bool = False,
        report: Optional[bool] = None,
    ) -> str:
        """Format a Hamlib rigctld-style response."""
        return rigctl_format_response(ok=ok, value=value, extended=extended, report=report)

    @staticmethod
    def normalize_rigctl_command(line: str) -> tuple[str, list[str], bool]:
        """Normalize common rigctld command spellings."""
        return rigctl_normalize_command(line)

    def rigctl_dump_state(self) -> str:
        """Return Hamlib NET rigctl protocol-0 dump_state."""
        return rigctl_dump_state_text()

    def rigctl_dump_caps(self) -> str:
        return rigctl_dump_caps_text(SCRIPT_VERSION)

    def rigctl_vfo_info(self) -> str:
        freq = getattr(self, "rigctl_freq_hz", 0)
        mode = getattr(self, "rigctl_mode", "USB")
        width = getattr(self, "rigctl_width", 0)
        vfo = getattr(self, "rigctl_vfo", "VFOA")
        return rigctl_vfo_info_text(freq, mode, width, vfo)

    @staticmethod
    def rigctl_bool(value: str) -> bool:
        return rigctl_bool_value(value)

    @staticmethod
    def parse_number(value: str) -> float:
        return rigctl_parse_number(value)

    @staticmethod
    def int_string(value: float | int) -> str:
        return rigctl_int_string(value)

    def rigctl_query_list(self, kind: str) -> str:
        return rigctl_query_list_text(kind)

    def rigctl_get_cached(self, store: dict[str, str], key: str, default: str = "0") -> str:
        return rigctl_get_cached_value(store, key, default)

    def rigctl_set_cached(self, store: dict[str, str], key: str, value: str) -> None:
        rigctl_set_cached_value(store, key, value)

    # Rigctl extension seam:
    #   handle_rigctl_line is intentionally still the compatibility boundary for
    #   Hamlib/local rigctl compatibility quirks.  Future cleanup should split it into small command
    #   groups (cat_getters, cat_setters, station_state, compatibility_noops)
    #   without changing normalize_rigctl_command() or rigctl_response() semantics.
    #   Known-real commands today: f/F, m/M and T/t.  Everything else should stay
    #   cached/no-op until its CI-V mapping is verified on the IC-705.

    def handle_rigctl_real_cat(
        self,
        cmd: str,
        args: list[str],
        extended: bool,
        *,
        radio_civ: Optional[int] = None,
    ) -> Optional[tuple[str, bool]]:
        return civ_handle_rigctl_real_cat(self, cmd, args, extended, radio_civ=radio_civ)

    def handle_rigctl_startup_compat(
        self,
        cmd: str,
        args: list[str],
        extended: bool,
        *,
        radio_civ: Optional[int] = None,
    ) -> Optional[tuple[str, bool]]:
        return rigctl_handle_startup_compat(self, cmd, args, extended, radio_civ=radio_civ)

    def handle_rigctl_station_state(
        self,
        cmd: str,
        args: list[str],
        extended: bool,
        *,
        radio_civ: Optional[int] = None,
    ) -> Optional[tuple[str, bool]]:
        return rigctl_handle_station_state(self, cmd, args, extended, radio_civ=radio_civ)

    def handle_rigctl_cached_families(
        self,
        cmd: str,
        args: list[str],
        extended: bool,
        *,
        radio_civ: Optional[int] = None,
    ) -> Optional[tuple[str, bool]]:
        return rigctl_handle_cached_families(self, cmd, args, extended, radio_civ=radio_civ)

    def handle_rigctl_misc_compat(
        self,
        cmd: str,
        args: list[str],
        extended: bool,
        *,
        radio_civ: Optional[int] = None,
    ) -> Optional[tuple[str, bool]]:
        return rigctl_handle_misc_compat(self, cmd, args, extended, radio_civ=radio_civ)

    def handle_rigctl_line(self, line: str, *, radio_civ: Optional[int] = None) -> tuple[str, bool]:
        return rigctl_handle_line(self, line, radio_civ=radio_civ)


    def station_rx_audio_bridge_loop(
        self,
        stop: threading.Event,
        *,
        audio_device: Optional[str] = None,
        output_gain: Optional[float] = None,
        device_sample_rate: Optional[int] = None,
    ) -> None:
        return station_rx_audio_bridge_loop_impl(
            self,
            stop,
            audio_device=audio_device,
            output_gain=output_gain,
            device_sample_rate=device_sample_rate,
        )

    def station_tx_audio_bridge_loop(
        self,
        stop: threading.Event,
        *,
        audio_device: Optional[str] = None,
        tx_local_input_gain: float = 1.0,
        device_sample_rate: Optional[int] = None,
    ) -> None:
        return station_tx_audio_bridge_loop_impl(
            self,
            stop,
            audio_device=audio_device,
            tx_local_input_gain=tx_local_input_gain,
            device_sample_rate=device_sample_rate,
        )

    def run_station(
        self,
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
        return station_run_station_impl(
            self,
            stop,
            rigctld_listen=rigctld_listen,
            rigctld_port=rigctld_port,
            radio_civ=radio_civ,
            rigctld_handshake=rigctld_handshake,
            rigctld_debug_bytes=rigctld_debug_bytes,
            rigctld_strict=rigctld_strict,
            allow_real_tune=allow_real_tune,
            real_cat_cache_ttl=real_cat_cache_ttl,
            station_rx_audio=station_rx_audio,
            station_rx_audio_device=station_rx_audio_device,
            station_rx_output_gain=station_rx_output_gain,
            station_rx_device_sample_rate=station_rx_device_sample_rate,
            station_tx_audio=station_tx_audio,
            station_tx_audio_device=station_tx_audio_device,
            station_tx_input_gain=station_tx_input_gain,
            station_tx_device_sample_rate=station_tx_device_sample_rate,
        )







def list_audio_devices() -> int:
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise SystemExit("list-audio-devices requires: python3 -m pip install sounddevice") from exc

    print(sd.query_devices())
    return 0


def describe_sounddevice_devices(kind: Optional[str] = None) -> str:
    return audio_describe_sounddevice_devices(kind)


def resolve_sounddevice_selector(device: Optional[str]) -> Optional[str | int]:
    return audio_resolve_sounddevice_selector(device)


def preflight_sounddevice_device(device: Optional[str], kind: str, label: str) -> None:
    audio_preflight_sounddevice_device(device, kind, label)

def rigctl_selftest(host: str, port: int, command: str, timeout: float = 3.0) -> int:
    payload = command if command.endswith("\n") else command + "\n"
    print(f"Connecting to {host}:{port}")
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        print(f">>> {payload.rstrip()}")
        sock.sendall(payload.encode("ascii"))
        chunks: list[bytes] = []
        while True:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
            if b"\n" in data:
                # Most of our first-pass replies are one or two lines.  Keep the
                # test quick rather than waiting for the server to close.
                break
    response = b"".join(chunks).decode("ascii", errors="replace")
    print("<<<", response.rstrip() if response else "(no response)")
    return 0

# Compatibility wrappers kept in cli.py because older tests and ad-hoc scripts
# import these names from icom_lan.cli.  Implementations live in command modules.
from .args import build_parser
from .commands.local import (
    describe_sounddevice_devices as _command_describe_sounddevice_devices,
    list_audio_devices as _command_list_audio_devices,
    preflight_sounddevice_device as _command_preflight_sounddevice_device,
    resolve_sounddevice_selector as _command_resolve_sounddevice_selector,
    rigctl_selftest as _command_rigctl_selftest,
)
from .commands.runtime import run_main as _run_main


def list_audio_devices() -> int:
    return _command_list_audio_devices()


def describe_sounddevice_devices(kind: Optional[str] = None) -> str:
    return _command_describe_sounddevice_devices(kind)


def resolve_sounddevice_selector(device: Optional[str]) -> Optional[str | int]:
    return _command_resolve_sounddevice_selector(device)


def preflight_sounddevice_device(device: Optional[str], kind: str, label: str) -> None:
    _command_preflight_sounddevice_device(device, kind, label)


def rigctl_selftest(host: str, port: int, command: str, timeout: float = 3.0) -> int:
    return _command_rigctl_selftest(host, port, command, timeout=timeout)


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return _run_main(args, IcomLanSession)


if __name__ == "__main__":
    raise SystemExit(main())
