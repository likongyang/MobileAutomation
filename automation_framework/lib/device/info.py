"""Device information collector."""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

from automation_framework.lib.adb.client import ADBClient

logger = logging.getLogger(__name__)


@dataclass
class DeviceInfo:
    """
    Represents a snapshot of basic device information collected before a test
    run begins.

    All string fields default to an empty string so that callers can safely
    format them without ``None``-checks.  The ``is_online`` and
    ``target_app_installed`` boolean flags default to the most conservative
    (safe) values.

    Attributes:
        device_id:             ADB serial of the device.
        brand:                 Manufacturer brand (e.g. ``"Google"``).
        model:                 Device model name (e.g. ``"Pixel 6"``).
        android_version:       Android release version (e.g. ``"13"``).
        sdk_version:           API level as a string (e.g. ``"33"``).
        resolution:            Screen resolution (e.g. ``"1080x2400"``).
        density:               Screen density (e.g. ``"420 dpi"``).
        total_memory:          Total RAM (e.g. ``"7.7 GB"``).
        available_memory:      Available RAM at collection time (e.g. ``"3.2 GB"``).
        cpu_abi:               Primary CPU ABI (e.g. ``"arm64-v8a"``).
        locale:                Device locale (e.g. ``"en-US"``).
        orientation:           Current orientation: ``"portrait"`` or ``"landscape"``.
        is_online:             Whether the device was reachable when info was collected.
        target_app_installed:  Whether the app under test is installed.
    """

    device_id: str
    brand: str = ""
    model: str = ""
    android_version: str = ""
    sdk_version: str = ""
    resolution: str = ""
    density: str = ""
    total_memory: str = ""
    available_memory: str = ""
    cpu_abi: str = ""
    locale: str = ""
    orientation: str = ""
    is_online: bool = True
    target_app_installed: bool = False

    def to_dict(self) -> dict:
        """
        Serialise the dataclass to a plain dictionary.

        Useful for embedding device info in JSON test reports or logging
        structured data.

        Returns:
            A ``dict`` with the same keys and values as the dataclass fields.
        """
        return asdict(self)


def collect_device_info(adb: ADBClient, app_package: str) -> DeviceInfo:
    """
    Collect device information using ADB and return a populated
    :class:`DeviceInfo` instance.

    Every field is collected on a best-effort basis.  Individual failures are
    caught and logged at ``DEBUG`` level; they do **not** propagate as
    exceptions.  This design ensures that a partial ADB failure (e.g. a
    property that does not exist on a particular ROM) does not abort test
    initialisation.

    Args:
        adb:         An :class:`~automation_framework.lib.adb.client.ADBClient`
                     already scoped to the target device.
        app_package: The fully-qualified package name of the application under
                     test (e.g. ``"com.example.myapp"``).  Used to populate
                     :attr:`DeviceInfo.target_app_installed`.

    Returns:
        A :class:`DeviceInfo` dataclass populated with whatever information
        could be retrieved.  Fields that could not be read are left at their
        default values.

    Example::

        adb = ADBClient("emulator-5554")
        info = collect_device_info(adb, "com.example.myapp")
        print(info.model)   # "Pixel 6"
        print(info.to_dict())
    """
    logger.info("Collecting device info for %s", adb.device_id)

    def safe(fn, default="unknown"):
        """Call *fn* and return its result; return *default* on any exception."""
        try:
            return fn()
        except Exception as exc:
            logger.debug("DeviceInfo collection error: %s", exc)
            return default

    info = DeviceInfo(
        device_id=adb.device_id,
        brand=safe(adb.get_brand),
        model=safe(adb.get_model),
        android_version=safe(adb.get_android_version),
        sdk_version=safe(adb.get_sdk_version),
        resolution=safe(adb.get_resolution),
        density=safe(adb.get_density),
        total_memory=safe(adb.get_total_memory),
        available_memory=safe(adb.get_available_memory),
        cpu_abi=safe(adb.get_cpu_abi),
        locale=safe(adb.get_locale),
        orientation=safe(adb.get_orientation),
        is_online=safe(adb.is_online, default=False),
        target_app_installed=safe(
            lambda: adb.is_app_installed(app_package), default=False
        ),
    )

    logger.debug(
        "Device info collected for %s: brand=%s model=%s android=%s sdk=%s",
        adb.device_id,
        info.brand,
        info.model,
        info.android_version,
        info.sdk_version,
    )
    return info
