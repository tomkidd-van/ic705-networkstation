from __future__ import annotations

import contextlib
import select
import socket
import struct
import sys
import time
from typing import Optional

from ..constants import CONTROL_SIZE, PING_SIZE, CONNINFO_SIZE, TOKEN_SIZE
from .network import make_my_id, now_ms_day


class UdpEndpoint:
    def __init__(self, local_ip: str, remote_ip: str, remote_port: int, local_port: int = 0, verbose: bool = False):
        self.local_ip = local_ip
        self.remote_ip = remote_ip
        self.remote_port = remote_port
        self.verbose = verbose
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((local_ip, local_port))
        self.sock.setblocking(False)
        self.local_port = self.sock.getsockname()[1]
        self.my_id = make_my_id(local_ip, self.local_port)
        self.remote_id = 0
        self.send_seq = 1
        self.send_seq_b = 0
        self.auth_seq = 0x30
        self.sent_packets: dict[int, bytes] = {}
        self.service_counts: dict[str, int] = {}
        self.running = True

    def bump_service_count(self, name: str, amount: int = 1) -> None:
        self.service_counts[name] = self.service_counts.get(name, 0) + amount

    def pop_service_counts(self) -> dict[str, int]:
        out = dict(self.service_counts)
        self.service_counts.clear()
        return out

    def close(self) -> None:
        self.running = False
        with contextlib.suppress(Exception):
            self.send_control(0x05, tracked=False, seq=0)
        self.sock.close()

    def log(self, *parts: object) -> None:
        if self.verbose:
            print("[udp]", *parts, file=sys.stderr)

    def send_raw(self, data: bytes) -> None:
        self.sock.sendto(data, (self.remote_ip, self.remote_port))

    def send_tracked(self, data: bytes) -> int:
        b = bytearray(data)
        struct.pack_into("<H", b, 0x06, self.send_seq)
        seq = self.send_seq
        self.sent_packets[seq] = bytes(b)
        if len(self.sent_packets) > 500:
            oldest = sorted(self.sent_packets)[0]
            self.sent_packets.pop(oldest, None)
        self.send_seq = (self.send_seq + 1) & 0xFFFF
        self.send_raw(bytes(b))
        return seq

    def send_control(self, packet_type: int, tracked: bool, seq: int = 0) -> None:
        pkt = struct.pack("<IHHII", CONTROL_SIZE, packet_type, seq, self.my_id, self.remote_id)
        if tracked:
            self.send_tracked(pkt)
        else:
            self.send_raw(pkt)

    def send_ping_reply(self, seq: int, radio_time: int) -> None:
        pkt = struct.pack("<IHHIIBI", PING_SIZE, 0x07, seq, self.my_id, self.remote_id, 0x01, radio_time)
        self.send_raw(pkt)

    def send_ping_request(self) -> None:
        pkt = struct.pack("<IHHIIBI", PING_SIZE, 0x07, 0, self.my_id, self.remote_id, 0x00, now_ms_day())
        self.send_raw(pkt)

    def service_until_empty(self, max_packets: int = 20) -> int:
        handled = 0
        for _ in range(max_packets):
            readable, _, _ = select.select([self.sock], [], [], 0)
            if not readable:
                return handled
            data, _addr = self.sock.recvfrom(65535)
            self._service_base_packet(data)
            handled += 1
        return handled

    def recv(self, timeout: float = 0.5) -> Optional[bytes]:
        readable, _, _ = select.select([self.sock], [], [], timeout)
        if not readable:
            return None
        data, _addr = self.sock.recvfrom(65535)
        self._service_base_packet(data)
        return data

    def _service_base_packet(self, data: bytes) -> None:
        if len(data) < CONTROL_SIZE:
            self.bump_service_count("short_rx")
            return
        pkt_len, pkt_type, seq, sentid, _rcvdid = struct.unpack_from("<IHHII", data, 0)

        if len(data) == CONTROL_SIZE:
            self.bump_service_count(f"control16_type_0x{pkt_type:02x}_rx")
        elif len(data) == PING_SIZE and pkt_type == 0x07:
            self.bump_service_count("ping_packet_rx")
        elif len(data) == CONNINFO_SIZE:
            self.bump_service_count("conninfo_rx")
        elif len(data) == TOKEN_SIZE:
            self.bump_service_count("token_packet_rx")
        else:
            self.bump_service_count(f"control_len_{len(data)}_type_0x{pkt_type:02x}_rx")

        if pkt_type == 0x01:
            self.bump_service_count("retransmit_request_rx")
            # Retransmit request.  Either one seq in a CONTROL_SIZE packet or a list from 0x10.
            seqs: list[int]
            if pkt_len == CONTROL_SIZE:
                seqs = [seq]
            else:
                seqs = []
                for off in range(0x10, min(len(data) - 1, pkt_len), 2):
                    seqs.append(struct.unpack_from("<H", data, off)[0])
            retransmitted = 0
            for s in seqs:
                if s in self.sent_packets:
                    self.send_raw(self.sent_packets[s])
                    retransmitted += 1
            if retransmitted:
                self.bump_service_count("retransmit_packet_tx", retransmitted)
            return

        if len(data) == PING_SIZE and pkt_type == 0x07:
            reply = data[0x10]
            if reply == 0x00:
                self.bump_service_count("ping_request_rx")
                radio_time = struct.unpack_from("<I", data, 0x11)[0]
                self.send_ping_reply(seq, radio_time)
                self.bump_service_count("ping_reply_tx")
            elif reply == 0x01:
                self.bump_service_count("ping_reply_rx")
            else:
                self.bump_service_count(f"ping_unknown_reply_0x{reply:02x}_rx")

    def wait_for_control_type(self, packet_type: int, timeout: float = 5.0) -> bytes:
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self.recv(deadline - time.time())
            if not data or len(data) < CONTROL_SIZE:
                continue
            pkt_len, typ, seq, sentid, _ = struct.unpack_from("<IHHII", data, 0)
            if pkt_len == CONTROL_SIZE and typ == packet_type:
                self.remote_id = sentid
                return data
        raise TimeoutError(f"Timed out waiting for control packet type 0x{packet_type:02x}")

