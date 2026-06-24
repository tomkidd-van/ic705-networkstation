"""Station RX/TX audio bridge loops.

These functions keep the live bridge implementation separate from the session
class while preserving the old IcomLanSession method wrappers as call sites.
"""

from __future__ import annotations

import contextlib
import sys
import time
from typing import Any, Optional

from ..audio import Pcm16MonoRateAdapter, resolve_sounddevice_selector
from ..constants import AUDIO_SIZE, TX_AUDIO_BLOCK_MS
from ..errors import ProtocolError


def station_rx_audio_bridge_loop(
    session: Any,
    stop,
    *,
    audio_device: Optional[str] = None,
    output_gain: Optional[float] = None,
    device_sample_rate: Optional[int] = None,
) -> None:
    """Station RX bridge: IC-705 LAN RX audio -> local RawOutputStream."""
    if session.audio is None:
        raise ProtocolError("Audio endpoint not open")
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise SystemExit("station RX audio bridge requires: python3 -m pip install sounddevice") from exc

    rx_output_gain = session.rx_gain if output_gain is None else float(output_gain)
    rx_device_sample_rate = int(device_sample_rate or session.rx_sample_rate)
    rx_rate_adapter = Pcm16MonoRateAdapter(session.rx_sample_rate, rx_device_sample_rate)
    resolved_audio_device = resolve_sounddevice_selector(audio_device)
    block_label = resolved_audio_device if resolved_audio_device is not None else "default"
    print(
        f"Station RX audio bridge enabled: device={block_label!r} "
        f"radio_sample_rate={session.rx_sample_rate} device_sample_rate={rx_device_sample_rate} "
        f"format=s16le mono local_playback_gain={rx_output_gain}"
    )
    if rx_rate_adapter.enabled:
        print(
            "Station RX audio resampler: "
            f"{session.rx_sample_rate} Hz radio -> {rx_device_sample_rate} Hz device"
        )
    stats_packets = 0
    stats_bytes = 0
    last_print = time.time()
    last_ping = 0.0

    with sd.RawOutputStream(
        samplerate=rx_device_sample_rate,
        channels=1,
        dtype="int16",
        latency="high",
        blocksize=0,
        device=resolved_audio_device,
    ) as stream:
        while not stop.is_set():
            now = time.time()
            if now - last_ping >= 0.1:
                with contextlib.suppress(Exception):
                    session.audio.send_ping_request()
                last_ping = now

            data = session.audio.recv(0.05)
            if not data:
                continue
            if len(data) <= AUDIO_SIZE:
                continue
            payload = data[AUDIO_SIZE:]
            if len(payload) % 2:
                payload = payload[:-1]
            if not payload:
                continue
            if rx_output_gain != 1.0:
                payload = session.pcm16le_apply_gain(payload, rx_output_gain)
            payload = rx_rate_adapter.process(payload)
            if not payload:
                continue
            stream.write(payload)
            stats_packets += 1
            stats_bytes += len(payload)
            if session.verbose and time.time() - last_print >= 5.0:
                print(
                    f"[station-rx-audio] packets={stats_packets} bytes={stats_bytes}",
                    file=sys.stderr,
                )
                last_print = time.time()


def station_tx_audio_bridge_loop(
    session: Any,
    stop,
    *,
    audio_device: Optional[str] = None,
    tx_local_input_gain: float = 1.0,
    device_sample_rate: Optional[int] = None,
) -> None:
    """Station TX bridge: local RawInputStream -> IC-705 LAN TX audio."""
    if session.audio is None:
        raise ProtocolError("Audio endpoint not open")
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise SystemExit("station TX audio bridge requires: python3 -m pip install sounddevice") from exc

    tx_device_sample_rate = int(device_sample_rate or session.tx_sample_rate)
    block_frames = max(1, int(tx_device_sample_rate * TX_AUDIO_BLOCK_MS))
    block_duration = block_frames / float(tx_device_sample_rate)
    tx_rate_adapter = Pcm16MonoRateAdapter(tx_device_sample_rate, session.tx_sample_rate)
    resolved_audio_device = resolve_sounddevice_selector(audio_device)
    device_label = resolved_audio_device if resolved_audio_device is not None else "default"
    print(
        f"Station TX audio bridge enabled: device={device_label!r} "
        f"device_sample_rate={tx_device_sample_rate} radio_sample_rate={session.tx_sample_rate} "
        f"format=s16le mono block_frames={block_frames} "
        f"tx_local_input_gain={tx_local_input_gain}; gated by confirmed rigctl PTT"
    )
    if tx_rate_adapter.enabled:
        print(
            "Station TX audio resampler: "
            f"{tx_device_sample_rate} Hz device -> {session.tx_sample_rate} Hz radio"
        )
    print(
        "Station TX audio pacing: wall-clock paced while keyed; "
        "unkeyed capture is continuously drained/discarded "
        f"block_duration={block_duration:.4f}s"
    )

    last_ping = 0.0
    was_ptt = False
    stats_packets = 0
    stats_bytes = 0
    burst_packets = 0
    burst_bytes = 0
    burst_frames = 0
    burst_started_at = 0.0
    burst_peak = 0
    burst_sum_squares = 0.0
    burst_sample_count = 0
    last_print = time.time()
    idle_overflow_count = 0
    keyed_overflow_count = 0
    last_overflow_print = time.time()
    session.collect_endpoint_service_counts()

    def maybe_print_overflow_summary(ptt_now: bool) -> None:
        nonlocal idle_overflow_count, keyed_overflow_count, last_overflow_print
        if not session.verbose:
            return
        now = time.time()
        if now - last_overflow_print < 5.0:
            return
        if idle_overflow_count or keyed_overflow_count:
            print(
                "[station-tx-audio] input overflow summary "
                f"idle={idle_overflow_count} keyed={keyed_overflow_count} ptt={int(ptt_now)}",
                file=sys.stderr,
            )
            idle_overflow_count = 0
            keyed_overflow_count = 0
            last_overflow_print = now

    def update_burst_audio_stats(payload: bytes) -> None:
        """Track simple PCM16LE peak/RMS stats for one keyed TX burst."""
        nonlocal burst_peak, burst_sum_squares, burst_sample_count
        if not session.verbose:
            return
        limit = len(payload) - (len(payload) % 2)
        for i in range(0, limit, 2):
            sample = int.from_bytes(payload[i:i + 2], "little", signed=True)
            abs_sample = abs(sample)
            if abs_sample > burst_peak:
                burst_peak = abs_sample
            burst_sum_squares += float(sample) * float(sample)
            burst_sample_count += 1

    def print_tx_bridge_summary(prefix: str) -> None:
        if not session.verbose:
            return
        counts = session.collect_endpoint_service_counts()
        count_text = session.format_service_counts(counts) if counts else "none"
        burst_device_audio_s = burst_frames / float(tx_device_sample_rate) if tx_device_sample_rate else 0.0
        burst_radio_audio_s = burst_bytes / 2.0 / float(session.tx_sample_rate) if session.tx_sample_rate else 0.0
        burst_wall_s = (time.perf_counter() - burst_started_at) if burst_started_at else 0.0
        burst_rms = (burst_sum_squares / burst_sample_count) ** 0.5 if burst_sample_count else 0.0
        print(
            f"[station-tx-audio] {prefix} packets={stats_packets} bytes={stats_bytes} "
            f"burst_packets={burst_packets} burst_bytes={burst_bytes} "
            f"burst_device_audio_s={burst_device_audio_s:.3f} "
            f"burst_radio_audio_s={burst_radio_audio_s:.3f} burst_wall_s={burst_wall_s:.3f} "
            f"burst_peak={burst_peak} burst_rms={burst_rms:.1f} "
            f"control={count_text}",
            file=sys.stderr,
        )

    with sd.RawInputStream(
        samplerate=tx_device_sample_rate,
        channels=1,
        dtype="int16",
        blocksize=block_frames,
        device=resolved_audio_device,
    ) as stream:
        while not stop.is_set():
            with contextlib.suppress(Exception):
                session.service_control_stationkeeping(max_packets=25)

            read_started_at = time.perf_counter()
            data, overflowed = stream.read(block_frames)
            read_elapsed = time.perf_counter() - read_started_at

            # Use the local TX audio gate, not the last confirmed radio PTT
            # state.  CAP-009 showed that radio PTT-off readback can lag a T 0
            # command long enough for one or more input blocks to be sent if
            # the audio loop keys directly from ptt_state.
            ptt_now = bool(getattr(session, "tx_audio_gate_enabled", False))
            if overflowed:
                if ptt_now:
                    keyed_overflow_count += 1
                else:
                    idle_overflow_count += 1
            maybe_print_overflow_summary(ptt_now)

            if ptt_now and not was_ptt:
                session.log("station TX audio gate opened")
                burst_packets = 0
                burst_bytes = 0
                burst_frames = 0
                burst_started_at = time.perf_counter()
                burst_peak = 0
                burst_sum_squares = 0.0
                burst_sample_count = 0
                session.collect_endpoint_service_counts()
                with contextlib.suppress(Exception):
                    session.service_control_stationkeeping(max_packets=50)
            if was_ptt and not ptt_now:
                session.log("station TX audio gate closed")
                print_tx_bridge_summary("gate-close")
                burst_started_at = 0.0
                with contextlib.suppress(Exception):
                    session.service_control_stationkeeping(max_packets=50)
            was_ptt = ptt_now

            if not ptt_now:
                # Keep draining and discarding capture audio while unkeyed so
                # ALSA/PortAudio buffers stay fresh for Direwolf/WSJT-X when
                # PTT opens.  v101 slept unconditionally here after an already
                # blocking read; on ALSA this drained at about half real-time and
                # caused repeated input overflows before TX.  Only add an
                # anti-spin sleep when a host API returns immediately.
                if read_elapsed < block_duration * 0.25:
                    time.sleep(min(block_duration * 0.25, 0.005))
                continue

            captured_payload = bytes(data)
            if len(captured_payload) % 2:
                captured_payload = captured_payload[:-1]
            if not captured_payload:
                continue
            if tx_local_input_gain != 1.0:
                captured_payload = session.pcm16le_apply_gain(captured_payload, tx_local_input_gain)
            update_burst_audio_stats(captured_payload)
            captured_frames = len(captured_payload) // 2

            payload = tx_rate_adapter.process(captured_payload)
            if not payload:
                # A downsampler may need more source samples before producing
                # the next radio-rate output sample.  Still pace by captured
                # device audio duration.
                burst_frames += captured_frames
                continue

            session.send_audio_payload(payload)
            stats_packets += 1
            stats_bytes += len(payload)
            burst_packets += 1
            burst_bytes += len(payload)
            burst_frames += captured_frames

            # Pace by local capture audio duration, not by how fast a particular
            # PortAudio host API returns RawInputStream.read().  This is
            # important on Windows virtual devices where blocking reads can
            # drain buffered samples far faster than wall-clock time.
            if burst_started_at:
                expected_elapsed = burst_frames / float(tx_device_sample_rate)
                actual_elapsed = time.perf_counter() - burst_started_at
                delay = expected_elapsed - actual_elapsed
                if delay > 0:
                    time.sleep(min(delay, block_duration * 2.0))

            with contextlib.suppress(Exception):
                session.service_control_stationkeeping(max_packets=25)

            now = time.time()
            if now - last_ping >= 0.1:
                with contextlib.suppress(Exception):
                    session.audio.send_ping_request()
                last_ping = now

            if session.verbose and now - last_print >= 5.0:
                print_tx_bridge_summary("running")
                last_print = now

    if was_ptt:
        print_tx_bridge_summary("stop")
