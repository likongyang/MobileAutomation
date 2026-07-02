"""Device discovery via adb devices."""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class DeviceState(str, Enum):
    """
    Represents the connection state of a discovered ADB device.

    Values correspond directly to the state strings emitted by ``adb devices``.
    Inheriting from ``str`` allows the enum to serialise transparently (e.g.
    when written to JSON or compared with raw strings).
    """

    ONLINE = "device"
    OFFLINE = "offline"
    UNAUTHORIZED = "unauthorized"
    UNKNOWN = "unknown"


@dataclass
class DiscoveredDevice:
    """
    A single device entry returned by ``adb devices``.

    Attributes:
        serial: The ADB serial / transport identifier (e.g. ``"emulator-5554"``
                or ``"192.168.1.10:5555"``).
        state:  The current connection state reported by ADB.
    """

    serial: str
    state: DeviceState

    @property
    def is_usable(self) -> bool:
        """
        Return ``True`` when the device is fully online and ready to accept
        ADB commands.

        Only devices in state :attr:`DeviceState.ONLINE` are considered usable.
        Offline or unauthorised devices are excluded so callers never attempt to
        run commands against unavailable hardware.
        """
        return self.state == DeviceState.ONLINE


def discover_devices() -> list[DiscoveredDevice]:
    """
    Run ``adb devices`` and return all discovered devices with their states.

    The first line of ``adb devices`` output (the ``"List of devices attached"``
    header) is skipped.  Each subsequent non-empty line that contains a tab
    character is parsed as ``<serial>\\t<state>``.

    Warnings are logged for offline and unauthorised devices so operators can
    take corrective action without enabling debug logging.

    Returns:
        A list of :class:`DiscoveredDevice` instances — may be empty if no
        devices are connected or if the command fails.

    Note:
        Only devices with :attr:`DiscoveredDevice.is_usable` set to ``True``
        (i.e. state ``"device"``) are safe to use for automation.  Use
        :func:`get_online_devices` for a filtered list of serials.
    """
    try:
        result = subprocess.run(
            ["adb", "devices"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        devices: list[DiscoveredDevice] = []
        lines = result.stdout.strip().split("\n")

        for line in lines[1:]:  # skip the "List of devices attached" header
            line = line.strip()
            if not line or "\t" not in line:
                continue

            serial, state_str = line.split("\t", 1)
            serial = serial.strip()
            state_str = state_str.strip()

            try:
                state = DeviceState(state_str)
            except ValueError:
                state = DeviceState.UNKNOWN

            devices.append(DiscoveredDevice(serial=serial, state=state))

            if state == DeviceState.OFFLINE:
                logger.warning("Device %s is OFFLINE", serial)
            elif state == DeviceState.UNAUTHORIZED:
                logger.warning(
                    "Device %s is UNAUTHORIZED — accept the USB debugging "
                    "prompt on the device screen",
                    serial,
                )

        return devices

    except subprocess.TimeoutExpired:
        logger.error("adb devices timed out after 10 s")
        return []
    except Exception as exc:
        logger.error("Failed to discover devices: %s", exc)
        return []


def get_online_devices(specified: list[str] | None = None) -> list[str]:
    """
    Return the serial numbers of all currently online ADB devices.

    Optionally filters the result to a caller-supplied allow-list of serials.
    If *specified* is provided, any serial that is not online is logged as an
    error so CI pipelines can detect mis-configured device pools.

    Args:
        specified: An optional list of ADB serials.  When given, only serials
                   that appear in both this list *and* the set of online devices
                   are returned.  ``None`` (default) returns all online devices.

    Returns:
        A list of ADB serial strings for devices that are currently online.
        May be empty if no devices are connected or none of the specified
        devices are available.

    Example::

        # All online devices
        serials = get_online_devices()

        # Only the two lab devices we care about
        serials = get_online_devices(["emulator-5554", "R5CT103ABCD"])
    """
    all_devices = discover_devices()
    online = [d.serial for d in all_devices if d.is_usable]

    if specified is not None:
        available = [s for s in specified if s in online]
        missing = [s for s in specified if s not in online]
        if missing:
            logger.error(
                "Specified device(s) not online: %s. Currently online: %s",
                missing,
                online,
            )
        return available

    return online
