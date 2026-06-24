"""Session lifecycle and endpoint-stationkeeping mixin for Icom LAN sessions.

This module intentionally keeps behaviour as methods on the session object.
The mixin owns login/token/stream allocation and shutdown ordering, while
``icom_lan.cli`` keeps command-line routing and the remaining compatibility
wrappers.
"""

from __future__ import annotations

import contextlib
import random
import struct
import time
from typing import Optional

from ..constants import *
from ..errors import ProtocolError, StreamAllocationError
from ..models import ConnInfoControl
from ..protocol import (
    UdpEndpoint,
    build_login_packet,
    build_stream_request_packet,
    build_token_packet,
    control_packet_summary,
    decode_stream_status_error,
    parse_conninfo_control_packet,
    parse_radio_capabilities,
    parse_stream_status_packet,
    reserve_udp_ports,
    short_hex,
    summarize_control_packets as protocol_summarize_control_packets,
)


class SessionLifecycleMixin:
    def set_session_phase(self, phase: str) -> None:
        """Record the current high-level session phase for diagnostics."""
        self.last_session_phase = phase
        self.last_session_phase_at = time.time()
        if getattr(self, "verbose", False):
            self.log("session phase:", phase)

    def record_session_error(self, phase: str, exc: BaseException, *, detail: Optional[str] = None) -> None:
        """Remember the most recent session/station error without changing control flow."""
        self.last_session_error_phase = phase
        self.last_session_error_class = type(exc).__name__
        self.last_session_error_message = str(exc)
        self.last_session_error_detail = detail
        self.last_session_error_at = time.time()
        self.session_error_count = int(getattr(self, "session_error_count", 0)) + 1
        with contextlib.suppress(Exception):
            self.bump_station_counter("session_error")
        detail_part = f" detail={detail}" if detail else ""
        self.log(
            "session error:",
            f"phase={phase}",
            f"class={type(exc).__name__}",
            f"message={str(exc)!r}" + detail_part,
        )

    def session_observation_summary(self) -> str:
        """Compact session phase/error summary for station health logs."""
        parts = [f"session_phase={getattr(self, 'last_session_phase', 'unknown')}"]
        phase_at = float(getattr(self, "last_session_phase_at", 0.0) or 0.0)
        if phase_at:
            parts.append(f"session_phase_age_s={int(max(0.0, time.time() - phase_at))}")
        error_class = getattr(self, "last_session_error_class", None)
        if error_class:
            error_at = float(getattr(self, "last_session_error_at", 0.0) or 0.0)
            error_age = int(max(0.0, time.time() - error_at)) if error_at else -1
            error_phase = getattr(self, "last_session_error_phase", "unknown")
            parts.append(f"last_error={error_class}@{error_phase}")
            if error_age >= 0:
                parts.append(f"last_error_age_s={error_age}")
            parts.append(f"session_errors={int(getattr(self, 'session_error_count', 0))}")
        return " ".join(parts)

    def reset_control_endpoint_for_login_retry(self) -> None:
        """Reopen the control UDP endpoint before retrying a timed-out login.

        The IC-705 can briefly ignore or delay a fresh login after a recently
        closed station session.  Reopening the local UDP endpoint gives the
        retry a fresh local port, my_id and token request while preserving the
        same user-visible process and command line.
        """
        self.set_session_phase("login:reset-control-endpoint")
        with contextlib.suppress(Exception):
            self.control.close()
        retry_local_port = int(getattr(self, "control_local_port", 0) or 0)
        # Keep a fixed derivation port on retry when requested.  In normal
        # operation this remains 0, so retries still get a fresh ephemeral port.
        self.control = UdpEndpoint(self.local_ip, self.host, self.control_port, retry_local_port, self.verbose)
        self.token_request = random.randint(1, 0xFFFF)
        self.token = 0
        self.radios = []
        self.selected_radio = None
        self.civ_local_port = 0
        self.audio_local_port = 0
        self.civ_remote_port = 0
        self.audio_remote_port = 0
        self.last_conninfo_control = None
        self.log(
            "login retry: reopened control endpoint",
            "local",
            self.local_ip,
            "control local port",
            self.control.local_port,
            "token_request",
            hex(self.token_request),
        )

    def establish_control_ready(self) -> None:
        """Probe the radio control port and send the ready packet."""
        self.set_session_phase("control:discovery")
        self.log("local", self.local_ip, "control local port", self.control.local_port)
        deadline = time.time() + CONTROL_DISCOVERY_TIMEOUT
        while time.time() < deadline:
            if self.stop_event is not None and self.stop_event.is_set():
                raise KeyboardInterrupt
            self.control.send_control(0x03, tracked=False, seq=0)
            data = self.control.recv(0.5)
            if data and len(data) == CONTROL_SIZE:
                _len, typ, seq, sentid, _ = struct.unpack_from("<IHHII", data, 0)
                if typ == 0x04:
                    self.control.remote_id = sentid
                    self.set_session_phase("control:discovered")
                    self.log("got I-am-here", hex(sentid))
                    break
        else:
            raise TimeoutError("No I-am-here response from radio control port")

        self.set_session_phase("control:ready")
        self.control.send_control(0x06, tracked=False, seq=1)
        # Some radios reply with ready; some may move quickly to login response handling.
        with contextlib.suppress(TimeoutError):
            self.control.wait_for_control_type(0x06, timeout=CONTROL_READY_TIMEOUT)

    def login_with_retries(self) -> None:
        """Login with a small retry window for stale-session startup timing."""
        last_error: Optional[Exception] = None
        for attempt in range(1, LOGIN_RETRY_ATTEMPTS + 1):
            if attempt > 1:
                backoff = LOGIN_RETRY_BACKOFF * (attempt - 1)
                self.log(
                    "login retry:",
                    f"attempt={attempt}/{LOGIN_RETRY_ATTEMPTS}",
                    f"backoff={backoff:.2f}s",
                    "reason=previous login response timed out",
                )
                deadline = time.time() + backoff
                while time.time() < deadline:
                    if self.stop_event is not None and self.stop_event.is_set():
                        raise KeyboardInterrupt
                    time.sleep(min(0.10, max(0.0, deadline - time.time())))
                self.reset_control_endpoint_for_login_retry()

            self.establish_control_ready()
            self.set_session_phase(f"login:send-attempt-{attempt}")
            self.send_login()
            try:
                self.set_session_phase(f"login:wait-attempt-{attempt}")
                self.wait_login_response(timeout=LOGIN_RESPONSE_TIMEOUT)
                self.set_session_phase("login:complete")
                return
            except TimeoutError as exc:
                self.record_session_error(f"login:wait-attempt-{attempt}", exc)
                last_error = exc
                if attempt >= LOGIN_RETRY_ATTEMPTS:
                    break
                self.log(
                    "login response timed out",
                    f"attempt={attempt}/{LOGIN_RETRY_ATTEMPTS}",
                    "action=reopen-control-and-retry",
                )

        if last_error is not None:
            raise last_error
        raise TimeoutError("Timed out waiting for login response")

    def connect(self, request_streams: bool = True) -> None:
        """Connect to the radio and optionally negotiate CI-V/audio streams.

        This remains the public lifecycle entry point.  The individual phases
        below are split into small helpers so startup/retry behaviour is easier
        to audit without changing the radio-facing sequence.
        """
        self.set_session_phase("connect:start")
        try:
            self.login_with_retries()
            self.confirm_login_token()
            if not request_streams:
                self.set_session_phase("connect:complete-no-streams")
                return
            self.prepare_radio_for_streams()
            self.connect_streams_with_retries()
        except Exception as exc:
            self.record_session_error(getattr(self, "last_session_phase", "connect"), exc)
            raise

    def confirm_login_token(self, timeout: float = 3.0) -> None:
        """Send token 0x02 after login and wait best-effort for the response."""
        self.set_session_phase("token:send-0x02")
        self.send_token(0x02)
        # After token success, keep servicing control traffic before requesting streams.
        with contextlib.suppress(TimeoutError):
            self.set_session_phase("token:wait-0x02")
            self.wait_token_response(timeout=timeout)

    def prepare_radio_for_streams(self) -> None:
        """Reserve local stream ports, collect capabilities and select a radio."""
        self.set_session_phase("stream:prepare-local-ports")
        self.prepare_stream_local_ports()
        self.set_session_phase("capability:collect")
        self.collect_capabilities(timeout=6.0)
        self.select_first_radio_or_raise("Login succeeded but no radio capability packet was received")

    def select_first_radio_or_raise(self, message: str) -> None:
        """Select the first parsed capability or raise a protocol error."""
        if not self.radios:
            raise ProtocolError(message)
        self.selected_radio = self.radios[0]

    def connect_streams_with_retries(self) -> None:
        """Request and open CI-V/audio streams with the existing retry policy."""
        last_error: Optional[Exception] = None
        for attempt in range(1, self.stream_retries + 1):
            if self.stop_event is not None and self.stop_event.is_set():
                raise KeyboardInterrupt
            try:
                self.perform_stream_open_attempt(attempt)
                self.set_session_phase("connect:complete")
                return
            except Exception as exc:
                last_error = exc
                if not self.handle_stream_open_failure(exc, attempt):
                    break

        assert last_error is not None
        raise last_error

    def perform_stream_open_attempt(self, attempt: int) -> None:
        """Perform one stream request/status/open attempt."""
        with contextlib.suppress(Exception):
            self.service_control_stationkeeping(max_packets=50)
        self.set_session_phase(f"stream:request-attempt-{attempt}")
        self.log(f"stream request attempt {attempt}/{self.stream_retries}")
        self.request_streams()
        self.set_session_phase(f"stream:wait-status-attempt-{attempt}")
        self.wait_stream_status()
        self.set_session_phase(f"stream:open-attempt-{attempt}")
        self.open_civ_and_audio()

    def handle_stream_open_failure(self, exc: Exception, attempt: int) -> bool:
        """Handle one failed stream attempt.

        Returns True when the caller should continue with the next stream
        attempt.  Returns False when the retry budget is exhausted and the
        caller should re-raise the last error.
        """
        self.record_session_error(getattr(self, "last_session_phase", "stream"), exc)
        self.log_stream_failure(exc, attempt)

        # Do not retry stream allocation after a local programming error.
        # Retrying while the first attempt's allocation is still open causes
        # misleading 0/0 stream-port errors.
        if isinstance(exc, (NameError, AttributeError, TypeError, ValueError)):
            raise exc

        self.civ_remote_port = 0
        self.audio_remote_port = 0

        if attempt >= self.stream_retries:
            return False

        # A 0/0 stream allocation with 0xfdffffff is most often a stale-session
        # / radio-transition state.  A stream-status timeout during startup can
        # represent the same transition.  For these startup-only failures,
        # release the current token, reopen control on a fresh local UDP port,
        # relogin and collect capabilities again before the next stream request.
        if isinstance(exc, (StreamAllocationError, TimeoutError)):
            self.relogin_and_refresh_for_stream_retry(attempt, exc)
            return True

        self.service_and_refresh_token_for_stream_retry(attempt)
        return True

    def log_stream_failure(self, exc: Exception, attempt: int) -> None:
        """Log stream attempt failures using the established wording."""
        if isinstance(exc, StreamAllocationError):
            self.log(
                "stream request failed:",
                "reason=allocation-unavailable",
                f"status_error=0x{exc.status_error:08x}",
                f"status={decode_stream_status_error(exc.status_error)}",
                f"ports={exc.civ_port}/{exc.audio_port}",
                f"attempt={attempt}/{self.stream_retries}",
            )
        elif isinstance(exc, TimeoutError):
            self.log(
                "stream request failed:",
                "reason=timeout",
                f"attempt={attempt}/{self.stream_retries}",
                repr(exc),
            )
        else:
            self.log("stream request failed:", repr(exc), f"attempt={attempt}/{self.stream_retries}")

    def relogin_and_refresh_for_stream_retry(self, attempt: int, exc: Exception) -> None:
        """Release the current token, relogin and refresh stream/capability state."""
        backoff = STREAM_RELOGIN_BACKOFF + (attempt - 1) * STREAM_RELOGIN_BACKOFF_STEP
        self.log(
            "stream retry backoff:",
            f"seconds={backoff:.2f}",
            "action=token-removal-relogin-and-refresh-stream-ports",
            f"failure_type={type(exc).__name__}",
        )
        with contextlib.suppress(Exception):
            self.close_stream_endpoints_for_shutdown()
        with contextlib.suppress(Exception):
            self.drain_control_packets("stream-retry-pre-token-removal", timeout=CONTROL_PRE_TOKEN_DRAIN_TIMEOUT)
        with contextlib.suppress(Exception):
            self.remove_control_token_for_shutdown(attempts=TOKEN_REMOVAL_ATTEMPTS)

        self.wait_for_retry_backoff(backoff, service_control=False)

        self.reset_control_endpoint_for_login_retry()
        self.login_with_retries()
        self.confirm_login_token()
        self.prepare_stream_local_ports()
        self.collect_capabilities(timeout=6.0)
        self.select_first_radio_or_raise("Login retry succeeded but no radio capability packet was received")

    def service_and_refresh_token_for_stream_retry(self, attempt: int) -> None:
        """Legacy light retry path: service control traffic and refresh token."""
        backoff = STREAM_RETRY_BASE_BACKOFF + (attempt - 1) * STREAM_RETRY_BACKOFF_STEP
        self.log(
            "stream retry backoff:",
            f"seconds={backoff:.2f}",
            "action=service-control-and-refresh-token",
        )
        self.wait_for_retry_backoff(backoff, service_control=True)
        with contextlib.suppress(Exception):
            self.send_token(0x02)
            self.wait_token_response(timeout=1.0)

    def wait_for_retry_backoff(self, seconds: float, *, service_control: bool) -> None:
        """Wait for a retry backoff, respecting stop_event and optional stationkeeping."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self.stop_event is not None and self.stop_event.is_set():
                raise KeyboardInterrupt
            if service_control:
                with contextlib.suppress(Exception):
                    self.service_control_stationkeeping(max_packets=25)
            time.sleep(min(STREAM_RETRY_CONTROL_SERVICE_INTERVAL, max(0.0, deadline - time.time())))
        if self.stop_event is not None and self.stop_event.is_set():
            raise KeyboardInterrupt

    def send_login(self) -> None:
        self.set_session_phase("login:send-packet")
        p = build_login_packet(
            my_id=self.control.my_id,
            remote_id=self.control.remote_id,
            auth_seq=self.control.auth_seq,
            token_request=self.token_request,
            username=self.username,
            password=self.password,
            client_name=self.client_name,
        )
        self.control.auth_seq = (self.control.auth_seq + 1) & 0xFFFF
        self.log("sending login")
        self.control.send_tracked(p)

    def wait_login_response(self, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self.control.recv(deadline - time.time())
            if not data:
                continue
            if len(data) == LOGIN_RESPONSE_SIZE:
                error = struct.unpack_from("<I", data, 0x30)[0]
                got_req = struct.unpack_from("<H", data, 0x1A)[0]
                self.token = struct.unpack_from("<I", data, 0x1C)[0]
                conn = data[0x40:0x50].split(b"\x00", 1)[0].decode(errors="replace")
                if error == 0xFEFFFFFF:
                    raise PermissionError("Invalid username/password from radio")
                if got_req != self.token_request:
                    raise ProtocolError(f"Token request mismatch: sent {self.token_request:#x}, got {got_req:#x}")
                self.log("login ok", "token", hex(self.token), "connection", conn)
                return
            self._maybe_parse_capabilities(data)
        raise TimeoutError("Timed out waiting for login response")

    def send_token(self, request_type: int) -> int:
        self.set_session_phase(f"token:send-0x{request_type:x}")
        p = build_token_packet(
            my_id=self.control.my_id,
            remote_id=self.control.remote_id,
            auth_seq=self.control.auth_seq,
            token_request=self.token_request,
            token=self.token,
            request_type=request_type,
        )
        self.control.auth_seq = (self.control.auth_seq + 1) & 0xFFFF
        self.log("sending token", hex(request_type))
        return self.control.send_tracked(p)

    def wait_token_response(self, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self.control.recv(deadline - time.time())
            if not data:
                continue
            if len(data) == TOKEN_SIZE:
                response = struct.unpack_from("<I", data, 0x30)[0]
                if response not in (0x00000000, 0xFFFFFFFF):
                    raise ProtocolError(f"Unexpected token response {response:#x}")
                self.set_session_phase("token:response")
                self.log("token response", hex(response))
                return
            self._maybe_parse_capabilities(data)
        raise TimeoutError("Timed out waiting for token response")

    @staticmethod
    def summarize_control_packets(counts: dict[tuple[int, int], int]) -> str:
        return protocol_summarize_control_packets(counts)

    def log_control_packet_detail(self, prefix: str, data: bytes) -> None:
        self.log(f"{prefix}: {control_packet_summary(data)} hex={short_hex(data)}")
        info = parse_conninfo_control_packet(data)
        if info is not None:
            self.last_conninfo_control = info
            self.log(f"{prefix} parsed conninfo:", info.summary())

    def drain_control_packets(self, label: str, timeout: float = CONTROL_PRE_TOKEN_DRAIN_TIMEOUT) -> int:
        """Drain/log already-pending control packets before a phase transition.

        This is diagnostic only.  It helps separate packets that were already
        queued after CI-V/audio close from packets observed after token removal.
        """
        if self.control is None:
            return 0

        deadline = time.time() + timeout
        counts: dict[tuple[int, int], int] = {}

        while time.time() < deadline:
            data = self.control.recv(max(0.0, deadline - time.time()))
            if not data:
                continue

            if len(data) >= CONTROL_SIZE:
                _pkt_len, typ, _seq, _sentid, _rcvdid = struct.unpack_from("<IHHII", data, 0)
            else:
                typ = -1

            counts[(len(data), typ)] = counts.get((len(data), typ), 0) + 1
            info = parse_conninfo_control_packet(data)
            if info is not None:
                self.last_conninfo_control = info
            if self.shutdown_control_debug:
                self.log_control_packet_detail(f"{label} control packet", data)

        total = sum(counts.values())
        if total:
            self.log(f"{label} control traffic drained:", self.summarize_control_packets(counts))
            if self.last_conninfo_control is not None:
                self.log(f"{label} parsed conninfo:", self.last_conninfo_control.summary())
        return total

    def wait_token_removal_response(self, timeout: float = TOKEN_REMOVAL_ACK_TIMEOUT) -> bool:
        """Best-effort wait/log for response after sendToken(0x01).

        The useful shutdown signal is a token-sized response packet with result
        0x0.  While waiting, other control packets may also arrive.  They may be
        keepalives, stream-close replies, status packets, retransmits or traffic
        queued before token removal.  Count and summarize them rather than
        naming them as a known packet class.
        """
        if self.control is None:
            return False

        deadline = time.time() + timeout
        control_traffic: dict[tuple[int, int], int] = {}

        while time.time() < deadline:
            data = self.control.recv(max(0.0, deadline - time.time()))
            if not data:
                continue

            if len(data) >= CONTROL_SIZE:
                _pkt_len, typ, seq, sentid, rcvdid = struct.unpack_from("<IHHII", data, 0)
            else:
                typ, seq, sentid, rcvdid = (-1, 0, 0, 0)

            if len(data) == TOKEN_SIZE:
                response = struct.unpack_from("<I", data, 0x30)[0]
                if control_traffic:
                    self.log(
                        "token removal non-token control traffic before ACK:",
                        self.summarize_control_packets(control_traffic),
                    )
                if self.shutdown_control_debug:
                    self.log_control_packet_detail("token removal ACK packet", data)
                self.log(
                    "token removal token response",
                    hex(response),
                    f"seq={seq}",
                    f"sentid=0x{sentid:08x}",
                    f"rcvdid=0x{rcvdid:08x}",
                )
                if response in (0x00000000, 0xFFFFFFFF):
                    return True
                return False

            control_traffic[(len(data), typ)] = control_traffic.get((len(data), typ), 0) + 1
            info = parse_conninfo_control_packet(data)
            if info is not None:
                self.last_conninfo_control = info
            if self.shutdown_control_debug:
                self.log_control_packet_detail("token removal pre-ACK control packet", data)

        if control_traffic:
            self.log(
                "token removal non-token control traffic before timeout:",
                self.summarize_control_packets(control_traffic),
            )
        self.log("token removal response wait timed out")
        return False

    def prepare_stream_local_ports(self) -> None:
        # Reserve then close temporary sockets so request can advertise likely-free ports.
        self.civ_local_port, self.audio_local_port = reserve_udp_ports(self.local_ip, count=2)
        self.log("reserved local ports", self.civ_local_port, self.audio_local_port)

    def collect_capabilities(self, timeout: float = 5.0, retry_token: bool = True) -> None:
        """Collect radio capability packet after token confirmation.

        In practice the IC-705 occasionally accepts login/token but the
        capability packet arrives later than the old 2 second window.  The control
        session must stay alive and continue processing packets; for this script,
        wait a little longer and optionally re-send token 0x02.
        """
        deadline = time.time() + timeout
        next_retry = time.time() + 1.0
        self.set_session_phase("capability:collecting")
        while time.time() < deadline and not self.radios:
            wait = max(0.05, min(0.25, deadline - time.time()))
            data = self.control.recv(wait)
            if data:
                self._maybe_parse_capabilities(data)
                continue
            if retry_token and time.time() >= next_retry and not self.radios:
                with contextlib.suppress(Exception):
                    self.log("capability wait: re-sending token 0x2")
                    self.send_token(0x02)
                next_retry = time.time() + 1.0

    def _maybe_parse_capabilities(self, data: bytes) -> bool:
        radios = parse_radio_capabilities(data)
        if radios:
            self.set_session_phase("capability:received")
            self.radios = radios
            for r in radios:
                self.log("capability", r)
            return True
        return False

    def request_streams(self) -> None:
        self.set_session_phase("stream:request-packet")
        assert self.selected_radio is not None
        p = build_stream_request_packet(
            my_id=self.control.my_id,
            remote_id=self.control.remote_id,
            auth_seq=self.control.auth_seq,
            token_request=self.token_request,
            token=self.token,
            radio=self.selected_radio,
            username=self.username,
            rx_codec=self.rx_codec,
            tx_codec=self.tx_codec,
            rx_sample_rate=self.rx_sample_rate,
            tx_sample_rate=self.tx_sample_rate,
            civ_local_port=self.civ_local_port,
            audio_local_port=self.audio_local_port,
            rx_enable=getattr(self, "stream_rx_enable", 1),
            tx_enable=getattr(self, "stream_tx_enable", 1),
            tx_buffer=getattr(self, "stream_tx_buffer", 200),
            convert=getattr(self, "stream_convert", 1),
        )
        self.control.auth_seq = (self.control.auth_seq + 1) & 0xFFFF
        self.log(
            "requesting streams",
            f"rxen={getattr(self, 'stream_rx_enable', 1)}",
            f"txen={getattr(self, 'stream_tx_enable', 1)}",
            f"rx_codec=0x{self.rx_codec:02x}",
            f"tx_codec=0x{self.tx_codec:02x}",
            f"rxsr={self.rx_sample_rate}",
            f"txsr={self.tx_sample_rate}",
            f"txbuf={getattr(self, 'stream_tx_buffer', 200)}",
            f"convert={getattr(self, 'stream_convert', 1)}",
        )
        self.control.send_tracked(p)

    def wait_stream_status(self, timeout: float = 5.0) -> None:
        self.set_session_phase("stream:wait-status")
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self.control.recv(deadline - time.time())
            if not data:
                continue
            status = parse_stream_status_packet(data)
            if status is not None:
                error = status.status_error
                disc = status.disc
                self.civ_remote_port = status.civ_remote_port
                self.audio_remote_port = status.audio_remote_port
                self.set_session_phase("stream:status-received")
                self.log(
                    "stream status",
                    f"civ_port={self.civ_remote_port}",
                    f"audio_port={self.audio_remote_port}",
                    f"status_error=0x{error:08x}",
                    f"status={decode_stream_status_error(error)}",
                    f"disc={disc}",
                )
                if error == 0xFFFFFFFF:
                    raise ProtocolError("Radio rejected stream request")
                if disc == 0x01:
                    raise ProtocolError("Radio reported disconnected stream")
                if self.civ_remote_port == 0 or self.audio_remote_port == 0:
                    raise StreamAllocationError(error, disc, self.civ_remote_port, self.audio_remote_port)
                return
            self._maybe_parse_capabilities(data)
        raise TimeoutError("Timed out waiting for stream status")

    def open_civ_and_audio(self) -> None:
        self.set_session_phase("stream:open-audio")
        # Audio endpoint is always opened.  CI-V is opened only for PTT/TX
        # subcommands so RX-only operation remains command-free.
        self.audio = UdpEndpoint(self.local_ip, self.host, self.audio_remote_port, self.audio_local_port, self.verbose)
        self.audio.remote_id = self.control.remote_id
        self.audio.send_control(0x03, tracked=False, seq=0)
        with contextlib.suppress(Exception):
            self.audio.wait_for_control_type(0x04, timeout=1.0)
            self.audio.send_control(0x06, tracked=False, seq=1)
            with contextlib.suppress(Exception):
                self.audio.wait_for_control_type(0x06, timeout=1.0)

        if self.enable_civ:
            self.set_session_phase("stream:open-civ")
            self.civ = UdpEndpoint(self.local_ip, self.host, self.civ_remote_port, self.civ_local_port, self.verbose)
            self.civ.remote_id = self.control.remote_id
            self.civ.send_control(0x03, tracked=False, seq=0)
            with contextlib.suppress(Exception):
                self.civ.wait_for_control_type(0x04, timeout=1.0)
                self.civ.send_control(0x06, tracked=False, seq=1)
                with contextlib.suppress(Exception):
                    self.civ.wait_for_control_type(0x06, timeout=1.0)
            self.civ_send_open(close=False)
            self.set_session_phase("stream:open-complete")
        else:
            self.civ = None
            self.set_session_phase("stream:open-complete")

    @staticmethod
    def format_service_counts(counts: dict[str, int]) -> str:
        return ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) if counts else "none"

    def collect_endpoint_service_counts(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for name, ep in (("control", self.control), ("audio", self.audio), ("civ", self.civ)):
            if ep is None:
                continue
            for key, value in ep.pop_service_counts().items():
                totals[f"{name}.{key}"] = totals.get(f"{name}.{key}", 0) + value
        return totals

    def service_control_stationkeeping(self, max_packets: int = 50) -> int:
        """Service control-channel packets without waiting.

        This is the small, safe stationkeeping primitive we can call from any
        longer-running command path.  The UdpEndpoint base handler already
        replies to radio ping requests, handles retransmit requests and counts
        packet classes; this wrapper makes sure those packets are not left
        queued until shutdown.
        """
        if self.control is None:
            return 0
        return self.control.service_until_empty(max_packets=max_packets)

    def close_stream_endpoints_for_shutdown(self) -> None:
        """Close negotiated CI-V/audio stream endpoints before token removal."""
        if self.civ is not None:
            try:
                self.log("closing CI-V data mode")
                self.civ_send_open(close=True)
                time.sleep(0.04)
            except Exception as exc:
                self.log("CI-V close failed:", repr(exc))
            try:
                self.log("closing local CI-V endpoint")
                self.civ.close()
            except Exception as exc:
                self.log("local CI-V endpoint close failed:", repr(exc))
            finally:
                self.civ = None

        if self.audio is not None:
            try:
                self.log("closing local audio endpoint")
                self.audio.close()
            except Exception as exc:
                self.log("local audio endpoint close failed:", repr(exc))
            finally:
                self.audio = None

    def remove_control_token_for_shutdown(self, attempts: int = TOKEN_REMOVAL_ATTEMPTS) -> bool:
        """Send token-removal request type 0x01 and wait briefly for diagnostics."""
        if self.control is None or not self.token:
            return False

        for i in range(attempts):
            try:
                self.log(f"sending token removal 0x01 attempt {i + 1}/{attempts}")
                self.send_token(0x01)
                if self.wait_token_removal_response(timeout=TOKEN_REMOVAL_ACK_TIMEOUT):
                    self.log("token removal acknowledged")
                    return True
            except Exception as exc:
                self.log("token removal failed:", repr(exc))
                break

        self.log("token removal acknowledgement not confirmed")
        return False

    def close_control_endpoint_for_shutdown(self) -> None:
        if self.control is not None:
            try:
                self.log("closing local control endpoint")
                self.control.close()
            except Exception as exc:
                self.log("local control endpoint close failed:", repr(exc))
            finally:
                self.control = None

    def close_radio_session(self) -> None:
        """Best-effort token-removal cleanup of the radio LAN session.

        Shutdown order:
          1. close negotiated CI-V/audio stream endpoints locally
          2. send token removal 0x01 while control is still open
          3. log any control/token response we see
          4. close local control endpoint

        Earlier versions sent token removal three times unconditionally.  v51
        proved the radio can ACK token removal with a 64-byte token response
        containing result 0x0.  v52 keeps UDP retry behaviour, but normally
        stops after the first acknowledged send.
        """
        self.set_session_phase("shutdown:start")
        self.log("closing radio session using token removal 0x01")
        self.tx_audio_gate_enabled = False
        if self.civ is not None:
            self.log("close_radio_session: sending repeated PTT OFF safety commands")
            with contextlib.suppress(Exception):
                self.send_ptt_off_recovery(reason="session-close")
            self.ptt_state = False
            self.ptt_radio_state = False
            time.sleep(0.05)
        self.close_stream_endpoints_for_shutdown()
        self.drain_control_packets("pre-token-removal", timeout=CONTROL_PRE_TOKEN_DRAIN_TIMEOUT)
        self.remove_control_token_for_shutdown(attempts=TOKEN_REMOVAL_ATTEMPTS)
        self.close_control_endpoint_for_shutdown()
        self.set_session_phase("shutdown:complete")

    def service_streams_once(self) -> int:
        handled = 0
        for ep in (self.control, self.audio):
            if ep is not None:
                with contextlib.suppress(Exception):
                    handled += ep.service_until_empty()
        return handled
