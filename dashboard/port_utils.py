"""Host port availability checks for LEco DevOps dashboard and compose scaffolding."""

from __future__ import annotations

import socket

# Hosted-app MongoDB host publish: never map container 27017 to host 27017 by default
# (Mac-native Mongo commonly uses 27017).
MONGO_HOST_PORT_CANDIDATES: tuple[int, ...] = (27018, 27019, 27020, 27021, 27022)
MONGO_CONTAINER_PORT = 27017


def host_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Return True if binding to host:port fails (port appears in use)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def pick_first_free_mongo_host_port(
    candidates: tuple[int, ...] | None = None,
    *,
    skip_ports: tuple[int, ...] = (27017,),
) -> int | None:
    """First candidate port that is not in skip_ports and not in use on loopback."""
    for port in candidates or MONGO_HOST_PORT_CANDIDATES:
        if port in skip_ports:
            continue
        if not host_port_in_use(port):
            return port
    return None


def mongo_compose_port_mapping(host_port: int | None = None) -> str | None:
    """Return compose ports entry 'HOST:27017' or None if no free host port."""
    hp = host_port if host_port is not None else pick_first_free_mongo_host_port()
    if hp is None:
        return None
    return f"{hp}:{MONGO_CONTAINER_PORT}"
