#!/usr/bin/env python3
"""Argument parser for the IC-705 Icom LAN command line."""

from __future__ import annotations

import argparse
import os

from .constants import DEFAULT_CONTROL_PORT, DEFAULT_RX_CODEC, DEFAULT_SAMPLE_RATE, DEFAULT_TX_CODEC, REAL_CAT_CACHE_TTL


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Self-contained receive-only Icom LAN audio client")
    p.add_argument("--host", default=os.environ.get("ICOM_HOST"), help="Radio IP/hostname. May also be set with ICOM_HOST")
    p.add_argument("--control-port", type=int, default=DEFAULT_CONTROL_PORT)
    p.add_argument(
        "--control-local-port",
        type=int,
        default=int(os.environ.get("ICOM_CONTROL_LOCAL_PORT", "0")),
        help=(
            "Optional local UDP source port for the control socket. "
            "Default 0 lets the OS choose. Intended for protocol derivation; "
            "may also be set with ICOM_CONTROL_LOCAL_PORT."
        ),
    )
    p.add_argument("--user", default=os.environ.get("ICOM_USER"), help="Icom LAN username configured in the radio. May also be set with ICOM_USER")
    p.add_argument("--password", default=os.environ.get("ICOM_PASSWORD"), help="Icom LAN password configured in the radio. May also be set with ICOM_PASSWORD")
    p.add_argument("--client-name", default="pyicom-rx")
    p.add_argument("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE)
    p.add_argument("--local-rx-playback-gain", type=float, default=1.0, help="Fixed playback gain only. Use 1.0 for digital-safe monitoring")
    p.add_argument("--rx-swap16", action="store_true", help="Interpret RX payload as big-endian int16 instead of little-endian")
    p.add_argument("--rx-invert", action="store_true", help="Invert RX audio polarity")
    p.add_argument("--local-rx-buffer-ms", type=int, default=500, help="Local playback ring buffer size in milliseconds")
    p.add_argument("--rx-stats-interval", type=float, default=2.0)
    p.add_argument("--rx-codec", type=lambda x: int(x, 0), default=DEFAULT_RX_CODEC)
    p.add_argument("--tx-codec", type=lambda x: int(x, 0), default=DEFAULT_TX_CODEC)
    p.add_argument(
        "--stream-profile",
        choices=("conservative", "rx-only-minimal"),
        default=os.environ.get("ICOM_STREAM_PROFILE", "conservative"),
        help=(
            "Stream request profile. conservative keeps the capture-confirmed station-safe baseline. "
            "rx-only-minimal uses the CAP-014-confirmed receive-only profile "
            "(txenable=0, tx_buffer=0) and is allowed only for RX/probe commands. "
            "May also be set with ICOM_STREAM_PROFILE."
        ),
    )
    p.add_argument("--radio-civ", type=lambda x: int(x, 0), default=None, help="CI-V address for PTT. Defaults to radio capability, e.g. IC-705 0xA4")
    p.add_argument("--stream-retries", type=int, default=3, help="Retry stream allocation if the radio returns ports 0/0")
    p.add_argument("--shutdown-control-debug", action="store_true", help="Log detailed shutdown control packets and hex while waiting for token-removal ACK")
    # CAP-011 protocol-derivation flags.  These override the accepted baseline
    # stream-request fields for one-variable-at-a-time A/B captures.
    p.add_argument("--stream-rx-enable", type=lambda x: int(x, 0), default=None, help="Lab override for stream request rxenable byte at 0x70")
    p.add_argument("--stream-tx-enable", type=lambda x: int(x, 0), default=None, help="Lab override for stream request txenable byte at 0x71")
    p.add_argument("--stream-rx-codec", type=lambda x: int(x, 0), default=None, help="Lab override for stream request RX codec byte at 0x72")
    p.add_argument("--stream-tx-codec", type=lambda x: int(x, 0), default=None, help="Lab override for stream request TX codec byte at 0x73")
    p.add_argument("--stream-rx-sample-rate", type=int, default=None, help="Lab override for stream request RX sample-rate field at 0x74")
    p.add_argument("--stream-tx-sample-rate", type=int, default=None, help="Lab override for stream request TX sample-rate field at 0x78")
    p.add_argument("--stream-tx-buffer", type=lambda x: int(x, 0), default=None, help="Lab override for stream request TX buffer field at 0x84")
    p.add_argument("--stream-convert", type=lambda x: int(x, 0), default=None, help="Lab override for stream request convert byte at 0x88")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("probe", help="Login/session/stream negotiation only, then print negotiated info")
    sub.add_parser("list-audio-devices", help="List local sounddevice/PortAudio devices and exit; no radio login")

    rst = sub.add_parser("rigctl-selftest", help="Connect to a running station rigctl port and send one command; no radio login")
    rst.add_argument("--host", default="127.0.0.1")
    rst.add_argument("--port", type=int, default=4532)
    rst.add_argument("--command", dest="rigctl_command", default="t")
    rst.add_argument("--timeout", type=float, default=3.0)
    ap = sub.add_parser("audio-probe", help="Receive audio UDP packets and print headers/sizes without playback")
    ap.add_argument("--seconds", type=float, default=10.0)
    rxa = sub.add_parser("rx-audio", help="Digital-safe live RX audio using direct RawOutputStream PCM16LE writes")
    rxa.add_argument("--audio-device", default=None, help="Optional output device name or index, e.g. plughw:Loopback,0,0")
    rxar = sub.add_parser("rx-audio-raw", help="Alias for rx-audio")
    rxar.add_argument("--audio-device", default=None, help="Optional output device name or index")
    sub.add_parser("rx-audio-buffered", help="Old callback/ring-buffer playback path for comparison only")
    sub.add_parser("rx-stdout", help="Write stripped PCM16LE payload bytes to stdout")
    rf = sub.add_parser("rx-file", help="Write stripped PCM16LE payload bytes to a file or FIFO")
    rf.add_argument("--output", required=True)
    ra = sub.add_parser("rx-aplay", help="Linux/ALSA convenience: feed stripped PCM16LE to aplay")
    ra.add_argument("--device", default=None, help="Optional ALSA device, for example hw:1,0 or plughw:1,0")
    rec = sub.add_parser("rx-record", help="Record exact RX payload bytes to raw and WAV files")
    rec.add_argument("--seconds", type=float, default=30.0)
    rec.add_argument("--prefix", default="ic705_rx")

    ptt = sub.add_parser("ptt", help="PTT pulse test using CI-V only")
    ptt.add_argument("--pulse", type=float, default=1.0)

    station = sub.add_parser("station", help="Own the radio session and expose a minimal local rigctl PTT server")
    station.add_argument("--rigctld-listen", default="0.0.0.0")
    station.add_argument("--rigctld-port", type=int, default=4532)
    station.add_argument("--rigctld-handshake", action="store_true", help="Send a simple rigctld banner line on client connect")
    station.add_argument("--rigctld-debug-bytes", action="store_true", help="Log raw bytes received from rigctl clients")
    station.add_argument("--rigctld-strict", action="store_true", help="Reject cached/no-op compatibility commands; useful to discover real client dependencies")
    station.add_argument("--real-cat-cache-ttl", type=float, default=REAL_CAT_CACHE_TTL, help="Seconds to cache real f/m CI-V reads. Default 0 disables cache and requires live replies")
    station.add_argument("--allow-real-tune", action="store_true", help="Allow rigctl F/M to send real CI-V set commands")
    station.add_argument("--station-rx-audio", action="store_true", help="Enable station RX audio bridge to a local output device")
    station.add_argument("--station-rx-audio-device", default=None, help="Output device name or numeric index for station RX audio bridge")
    station.add_argument("--station-rx-output-gain", type=float, default=None, help="Station RX local output gain before playback; defaults to --local-rx-playback-gain")
    station.add_argument("--station-rx-device-sample-rate", type=int, default=None, help="Local output device sample rate for station RX audio. Default uses the radio RX stream rate.")
    station.add_argument("--station-tx-audio", action="store_true", help="Enable station TX audio bridge from a local input device, gated by rigctl PTT")
    station.add_argument("--station-tx-audio-device", default=None, help="Input device name or numeric index for station TX audio bridge")
    station.add_argument("--station-tx-input-gain", type=float, default=1.0, help="Fixed scalar for station TX input PCM before packetization; 1.0 is direct")
    station.add_argument("--station-tx-device-sample-rate", type=int, default=None, help="Local input device sample rate for station TX audio. Default uses the radio TX stream rate.")

    iw = sub.add_parser("inspect-wav", help="Inspect whether a WAV file is valid for direct tx-wav; no radio login required")
    iw.add_argument("--file", required=True)
    iw.add_argument("--expect-rate", type=int, default=None, help="Expected sample rate; defaults to --sample-rate")

    tw = sub.add_parser("tx-wav", help="Transmit audio from a WAV file with PTT")
    tw.add_argument("--file", required=True)
    tw.add_argument("--tx-local-input-gain", type=float, default=0.20, help="Local TX sample scalar before packetization; not a radio setting")
    tw.add_argument("--dry-run", action="store_true", help="Inspect and validate the WAV without keying PTT or sending audio")

    ti = sub.add_parser("tx-input", help="Transmit audio from a local input device with PTT")
    ti.add_argument("--seconds", type=float, default=5.0)
    ti.add_argument("--audio-device", default=None, help="Optional input device name or index, e.g. Linux loopback capture")
    ti.add_argument("--tx-local-input-gain", type=float, default=0.20, help="Local TX sample scalar before packetization; not a radio setting")
    return p
