from __future__ import annotations

import ipaddress
import socket
import time


def now_ms_day() -> int:
    return int((time.time() % 86400) * 1000)


def discover_local_ip(remote_host: str, remote_port: int) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((remote_host, remote_port))
        return s.getsockname()[0]
    finally:
        s.close()


def make_my_id(local_ip: str, local_port: int) -> int:
    # Observed Icom LAN client identifier format: ((addr >> 8) & 0xff) << 24 | (addr & 0xff) << 16 | localPort
    addr = int(ipaddress.IPv4Address(local_ip))
    return (((addr >> 8) & 0xFF) << 24) | ((addr & 0xFF) << 16) | (local_port & 0xFFFF)


def reserve_udp_ports(local_ip: str, count: int = 2) -> list[int]:
    """Reserve ephemeral UDP ports briefly and return the chosen port numbers."""
    sockets: list[socket.socket] = []
    try:
        for _ in range(count):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind((local_ip, 0))
            sockets.append(sock)
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()
