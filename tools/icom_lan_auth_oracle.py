#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Black-box Icom LAN authentication oracle probe.

This derivation helper sends a login packet with explicit 16-byte encoded
username/password fields and reports whether the radio accepted the login.  It
is deliberately independent of the runtime credential encoder: it contains no
credential lookup data and imports no auth/passcode helper.

Use this only on your own radio, on a trusted LAN and with test credentials.
Rate-limit attempts and restore original credentials after experiments.
"""
from __future__ import annotations

import argparse
import ipaddress
import random
import select
import socket
import struct
import sys
import time
from dataclasses import dataclass

CONTROL_SIZE = 0x10
LOGIN_SIZE = 0x80
LOGIN_RESPONSE_SIZE = 0x60
DEFAULT_CONTROL_PORT = 50001


@dataclass(frozen=True)
class LoginResult:
    accepted: bool
    response_code: int | None
    token_request: int
    token: int | None


def parse_hex_16(value: str) -> bytes:
    cleaned = value.replace(":", " ").replace(",", " ").replace("-", " ")
    parts = cleaned.split()
    if len(parts) == 1 and len(parts[0]) == 32:
        data = bytes.fromhex(parts[0])
    else:
        data = bytes(int(p, 16) for p in parts)
    if len(data) != 16:
        raise argparse.ArgumentTypeError(f"expected exactly 16 bytes, got {len(data)}")
    return data


def discover_local_ip(remote_host: str, remote_port: int) -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((remote_host, remote_port))
        return sock.getsockname()[0]
    finally:
        sock.close()


def make_my_id(local_ip: str, local_port: int) -> int:
    addr = int(ipaddress.IPv4Address(local_ip))
    return (((addr >> 8) & 0xFF) << 24) | ((addr & 0xFF) << 16) | (local_port & 0xFFFF)


def zpad_ascii(text: str, size: int) -> bytes:
    return text.encode("ascii", errors="replace")[:size].ljust(size, b"\x00")


class AuthOracle:
    def __init__(self, host: str, port: int, timeout: float, verbose: bool) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.verbose = verbose
        self.local_ip = discover_local_ip(host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.local_ip, 0))
        self.sock.setblocking(False)
        self.local_port = self.sock.getsockname()[1]
        self.my_id = make_my_id(self.local_ip, self.local_port)
        self.remote_id = 0
        self.send_seq = 1
        self.auth_seq = 0x30

    def close(self) -> None:
        self.sock.close()

    def log(self, *parts: object) -> None:
        if self.verbose:
            print("[auth-oracle]", *parts, file=sys.stderr)

    def send_raw(self, data: bytes) -> None:
        self.sock.sendto(data, (self.host, self.port))

    def recv(self, timeout: float) -> bytes | None:
        readable, _, _ = select.select([self.sock], [], [], timeout)
        if not readable:
            return None
        data, _addr = self.sock.recvfrom(65535)
        return data

    def send_control(self, packet_type: int, seq: int = 0) -> None:
        self.send_raw(struct.pack("<IHHII", CONTROL_SIZE, packet_type, seq, self.my_id, self.remote_id))

    def send_tracked(self, data: bytes) -> None:
        pkt = bytearray(data)
        struct.pack_into("<H", pkt, 0x06, self.send_seq)
        self.send_seq = (self.send_seq + 1) & 0xFFFF
        self.send_raw(bytes(pkt))

    def discover(self) -> None:
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            self.send_control(0x03, seq=0)
            data = self.recv(0.25)
            if not data or len(data) < CONTROL_SIZE:
                continue
            pkt_len, pkt_type, seq, sentid, rcvdid = struct.unpack_from("<IHHII", data, 0)
            self.log(f"rx len={len(data)} pkt_len={pkt_len} type=0x{pkt_type:02x} seq={seq} sentid=0x{sentid:08x} rcvdid=0x{rcvdid:08x}")
            if len(data) == CONTROL_SIZE and pkt_type == 0x04:
                self.remote_id = sentid
                return
        raise TimeoutError("no I-am-here response from radio")

    def ready(self) -> None:
        self.send_control(0x06, seq=1)
        end = time.time() + 0.5
        while time.time() < end:
            data = self.recv(0.05)
            if data:
                self.log("ready/drain", data[:32].hex(" "))

    def build_login(self, encoded_user: bytes, encoded_password: bytes, client_name: str, token_request: int) -> bytes:
        pkt = bytearray(LOGIN_SIZE)
        struct.pack_into("<I", pkt, 0x00, LOGIN_SIZE)
        struct.pack_into("<I", pkt, 0x08, self.my_id)
        struct.pack_into("<I", pkt, 0x0C, self.remote_id)
        struct.pack_into(">I", pkt, 0x10, LOGIN_SIZE - 0x10)
        pkt[0x14] = 0x01
        pkt[0x15] = 0x00
        struct.pack_into(">H", pkt, 0x16, self.auth_seq & 0xFFFF)
        struct.pack_into("<H", pkt, 0x1A, token_request & 0xFFFF)
        pkt[0x40:0x50] = encoded_user
        pkt[0x50:0x60] = encoded_password
        pkt[0x60:0x70] = zpad_ascii(client_name, 16)
        self.auth_seq = (self.auth_seq + 1) & 0xFFFF
        return bytes(pkt)

    def login(self, encoded_user: bytes, encoded_password: bytes, client_name: str) -> LoginResult:
        self.discover()
        self.ready()
        token_request = random.randint(1, 0xFFFF)
        self.send_tracked(self.build_login(encoded_user, encoded_password, client_name, token_request))
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            data = self.recv(max(0.0, deadline - time.time()))
            if not data:
                continue
            self.log("login rx", f"len={len(data)}", data[:32].hex(" "))
            if len(data) != LOGIN_RESPONSE_SIZE:
                continue
            got_req = struct.unpack_from("<H", data, 0x1A)[0]
            if got_req != token_request:
                continue
            token = struct.unpack_from("<I", data, 0x1C)[0]
            response = struct.unpack_from("<I", data, 0x30)[0]
            return LoginResult(response == 0x00000000, response, token_request, token)
        return LoginResult(False, None, token_request, None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", required=True)
    ap.add_argument("--control-port", type=int, default=DEFAULT_CONTROL_PORT)
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--client-name", default="auth-oracle")
    ap.add_argument("--user-encoded-hex", type=parse_hex_16, required=True)
    ap.add_argument("--pass-encoded-hex", type=parse_hex_16, required=True)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    oracle = AuthOracle(args.host, args.control_port, args.timeout, args.verbose)
    try:
        result = oracle.login(args.user_encoded_hex, args.pass_encoded_hex, args.client_name)
    finally:
        oracle.close()

    print(f"accepted={int(result.accepted)}")
    print("response=" + ("none" if result.response_code is None else f"0x{result.response_code:08x}"))
    print(f"token_request=0x{result.token_request:04x}")
    if result.token is not None:
        print(f"token=0x{result.token:08x}")
    return 0 if result.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
