"""Host port availability checks."""

from __future__ import annotations

import socket
from collections.abc import Iterable


def host_port_in_use(port: int, host: str = "0.0.0.0") -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def check_host_ports(ports: Iterable[int]) -> dict[int, bool]:
    """True means port appears in use (bind failed)."""
    return {p: host_port_in_use(p) for p in sorted(set(ports))}
