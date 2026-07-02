"""
Port allocation for Appium Server, systemPort and mjpeg port.
Ensures no two devices share the same ports during parallel execution.
"""
from __future__ import annotations

import socket
import threading
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_allocated: set[int] = set()


@dataclass
class DevicePorts:
    device_id: str
    appium_port: int
    system_port: int
    mjpeg_port: int

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "appium_port": self.appium_port,
            "system_port": self.system_port,
            "mjpeg_port": self.mjpeg_port,
        }


def _is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def _find_free_port(start: int, step: int = 1, max_tries: int = 50) -> int:
    for i in range(max_tries):
        port = start + i * step
        with _lock:
            if port not in _allocated and _is_port_free(port):
                _allocated.add(port)
                return port
    raise RuntimeError(f"Could not find a free port starting at {start} within {max_tries} attempts")


def allocate_ports(
    device_id: str,
    appium_base: int = 4723,
    system_base: int = 8200,
    mjpeg_base: int = 9100,
    device_index: int = 0,
) -> DevicePorts:
    """
    Allocate three non-conflicting ports for a device.
    Uses device_index as an initial offset, then finds a free port.
    """
    offset = device_index * 2
    appium_port = _find_free_port(appium_base + offset)
    system_port = _find_free_port(system_base + offset)
    mjpeg_port = _find_free_port(mjpeg_base + offset)

    ports = DevicePorts(
        device_id=device_id,
        appium_port=appium_port,
        system_port=system_port,
        mjpeg_port=mjpeg_port,
    )
    logger.info(
        "Allocated ports for %s: appium=%d, system=%d, mjpeg=%d",
        device_id, appium_port, system_port, mjpeg_port
    )
    return ports


def release_ports(ports: DevicePorts) -> None:
    """Release ports back to the pool."""
    with _lock:
        _allocated.discard(ports.appium_port)
        _allocated.discard(ports.system_port)
        _allocated.discard(ports.mjpeg_port)
