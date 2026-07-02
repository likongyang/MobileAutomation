"""
ADB client - wraps all adb command interactions.
Business code should NOT call adb directly; use this class instead.
"""
from __future__ import annotations

import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ADBError(Exception):
    """Raised when an ADB command fails."""


class ADBClient:
    """
    ADB command wrapper for a single device.

    All commands are scoped to ``self.device_id`` via ``adb -s <device_id>``.
    Business-layer code must not call ``adb`` directly; it should go through
    this class so that command routing, logging, and error handling remain
    consistent across the framework.

    Attributes:
        device_id: The ADB serial / transport identifier of the target device.
        timeout:   Default command timeout in seconds (can be overridden per-call).

    Example::

        client = ADBClient("emulator-5554")
        print(client.get_model())           # e.g. "Pixel 6"
        print(client.get_android_version()) # e.g. "13"
    """

    DEFAULT_TIMEOUT = 30  # seconds

    def __init__(self, device_id: str, timeout: int = DEFAULT_TIMEOUT) -> None:
        """
        Initialise an ADBClient scoped to *device_id*.

        Args:
            device_id: ADB serial of the target device (e.g. ``"emulator-5554"``
                       or ``"192.168.1.10:5555"``).
            timeout:   Default timeout (seconds) applied to every command unless
                       an individual call overrides it.
        """
        self.device_id = device_id
        self.timeout = timeout

    # ─── Low-level execution ───────────────────────────────────────────────────

    def run(
        self,
        *args: str,
        timeout: int | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        """
        Run an ``adb`` command scoped to this device.

        The full command issued is::

            adb -s <device_id> <args…>

        Args:
            *args:   Positional arguments appended after ``adb -s <device_id>``.
                     Example: ``run('shell', 'getprop', 'ro.product.model')``.
            timeout: Override the instance-level timeout for this call only.
            check:   If *True* (default), raise :class:`ADBError` when the
                     process exits with a non-zero return code.

        Returns:
            The completed process object with ``stdout``, ``stderr``, and
            ``returncode`` attributes.

        Raises:
            ADBError: If the command exits with a non-zero code (and
                      *check* is ``True``) or if it times out.
        """
        cmd = ["adb", "-s", self.device_id] + list(args)
        t = timeout if timeout is not None else self.timeout
        logger.debug("ADB: %s", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=t,
            )
            if check and result.returncode != 0:
                raise ADBError(
                    f"ADB command failed (rc={result.returncode}): {' '.join(cmd)}\n"
                    f"stderr: {result.stderr.strip()}"
                )
            return result
        except subprocess.TimeoutExpired:
            raise ADBError(
                f"ADB command timed out after {t}s: {' '.join(cmd)}"
            )

    def shell(
        self,
        command: str,
        timeout: int | None = None,
        check: bool = True,
    ) -> str:
        """
        Run ``adb shell <command>`` and return stripped stdout.

        Args:
            command: The shell command to execute on the device.
            timeout: Override the instance-level timeout for this call only.
            check:   Propagated to :meth:`run`.

        Returns:
            The stripped stdout of the command.

        Raises:
            ADBError: If the underlying :meth:`run` call raises.
        """
        result = self.run("shell", command, timeout=timeout, check=check)
        return result.stdout.strip()

    def run_custom(
        self,
        *args: str,
        timeout: int | None = None,
    ) -> tuple[int, str, str]:
        """
        Execute a custom ADB command and return raw result components.

        Designed for business-side extensions that need full control over
        exit-code handling without catching :class:`ADBError`.

        Args:
            *args:   Arguments forwarded to :meth:`run` (with ``check=False``).
            timeout: Override the instance-level timeout for this call only.

        Returns:
            A three-tuple ``(returncode, stdout, stderr)``.
        """
        result = self.run(*args, timeout=timeout, check=False)
        return result.returncode, result.stdout, result.stderr

    # ─── Device status ─────────────────────────────────────────────────────────

    def is_online(self) -> bool:
        """
        Return ``True`` if the device is currently reachable via ADB.

        Runs ``adb devices`` and checks whether this device's serial appears
        with state ``device`` (i.e. fully authorised and online).

        Returns:
            ``True`` if the device is online, ``False`` otherwise (including
            on any subprocess error).
        """
        try:
            result = subprocess.run(
                ["adb", "devices"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.strip().split("\n")[1:]:
                if "\t" in line:
                    serial, state = line.split("\t", 1)
                    if (
                        serial.strip() == self.device_id
                        and state.strip() == "device"
                    ):
                        return True
            return False
        except Exception:
            return False

    # ─── Device info ───────────────────────────────────────────────────────────

    def get_prop(self, prop: str) -> str:
        """
        Read a system property via ``getprop``.

        Args:
            prop: The property key (e.g. ``"ro.product.model"``).

        Returns:
            The property value, or an empty string if unavailable.
        """
        try:
            return self.shell(f"getprop {prop}", check=False)
        except ADBError:
            return ""

    def get_brand(self) -> str:
        """Return the device brand (e.g. ``"Google"``, ``"Samsung"``)."""
        return self.get_prop("ro.product.brand")

    def get_model(self) -> str:
        """Return the device model name (e.g. ``"Pixel 6"``)."""
        return self.get_prop("ro.product.model")

    def get_android_version(self) -> str:
        """Return the Android release version string (e.g. ``"13"``)."""
        return self.get_prop("ro.build.version.release")

    def get_sdk_version(self) -> str:
        """Return the Android SDK API level as a string (e.g. ``"33"``)."""
        return self.get_prop("ro.build.version.sdk")

    def get_cpu_abi(self) -> str:
        """Return the primary CPU ABI (e.g. ``"arm64-v8a"``)."""
        return self.get_prop("ro.product.cpu.abi")

    def get_resolution(self) -> str:
        """
        Return screen resolution as ``'<width>x<height>'`` (e.g. ``"1080x2400"``).

        Reads ``wm size`` and extracts the physical or override resolution.
        Falls back to ``"unknown"`` when the value cannot be parsed.
        """
        try:
            out = self.shell("wm size", check=False)
            # Physical size: "Physical size: 1080x2400"
            match = re.search(r"Physical size:\s+(\d+x\d+)", out)
            if match:
                return match.group(1)
            # Override size: "Override size: 1080x2400"
            match = re.search(r"Override size:\s+(\d+x\d+)", out)
            if match:
                return match.group(1)
            # Bare resolution anywhere in output
            match = re.search(r"(\d+x\d+)", out)
            if match:
                return match.group(1)
        except Exception:
            pass
        return "unknown"

    def get_density(self) -> str:
        """
        Return screen density as a human-readable string (e.g. ``"420 dpi"``).

        Falls back to ``"unknown"`` when the value cannot be parsed.
        """
        try:
            out = self.shell("wm density", check=False)
            match = re.search(r"(\d+)", out)
            if match:
                return f"{match.group(1)} dpi"
        except Exception:
            pass
        return "unknown"

    def get_total_memory(self) -> str:
        """
        Return total device RAM as a human-readable string (e.g. ``"7.7 GB"``).

        Reads ``/proc/meminfo`` and converts the ``MemTotal`` kB value.
        Falls back to ``"unknown"`` on any failure.
        """
        try:
            out = self.shell("cat /proc/meminfo", check=False)
            match = re.search(r"MemTotal:\s+(\d+)\s+kB", out)
            if match:
                kb = int(match.group(1))
                return f"{kb / 1024 / 1024:.1f} GB"
        except Exception:
            pass
        return "unknown"

    def get_available_memory(self) -> str:
        """
        Return available device RAM as a human-readable string (e.g. ``"3.2 GB"``).

        Reads ``/proc/meminfo`` and converts the ``MemAvailable`` kB value.
        Falls back to ``"unknown"`` on any failure.
        """
        try:
            out = self.shell("cat /proc/meminfo", check=False)
            match = re.search(r"MemAvailable:\s+(\d+)\s+kB", out)
            if match:
                kb = int(match.group(1))
                return f"{kb / 1024 / 1024:.1f} GB"
        except Exception:
            pass
        return "unknown"

    def get_locale(self) -> str:
        """
        Return the device locale string (e.g. ``"zh-CN"``, ``"en-US"``).

        Reads ``persist.sys.locale`` first; falls back to combining
        ``persist.sys.language`` and ``persist.sys.country``.
        Returns ``"unknown"`` if no locale information is found.
        """
        locale = self.get_prop("persist.sys.locale")
        if locale:
            return locale
        lang = self.get_prop("persist.sys.language")
        region = self.get_prop("persist.sys.country")
        if lang and region:
            return f"{lang}-{region}"
        return lang or "unknown"

    def get_orientation(self) -> str:
        """
        Return the current screen orientation as ``"portrait"`` or ``"landscape"``.

        Reads ``SurfaceOrientation`` from ``dumpsys input``.  Orientations 1 and
        3 are landscape; 0 and 2 are portrait.  Defaults to ``"portrait"`` on
        any failure.
        """
        try:
            out = self.shell(
                "dumpsys input | grep 'SurfaceOrientation'", check=False
            )
            if "SurfaceOrientation: 1" in out or "SurfaceOrientation: 3" in out:
                return "landscape"
            return "portrait"
        except Exception:
            return "portrait"

    # ─── App management ────────────────────────────────────────────────────────

    def is_app_installed(self, package: str) -> bool:
        """
        Return ``True`` if *package* is installed on the device.

        Uses ``pm list packages`` to check for the package name.

        Args:
            package: The fully-qualified Android package name
                     (e.g. ``"com.example.myapp"``).
        """
        out = self.shell(f"pm list packages {package}", check=False)
        return f"package:{package}" in out

    def install_apk(self, apk_path: str) -> None:
        """
        Install an APK file onto the device.

        Uses ``adb install -r -g`` which reinstalls over existing versions and
        grants all requested permissions automatically.

        Args:
            apk_path: Local path to the APK file.

        Raises:
            ADBError: If installation fails.
        """
        logger.info("Installing APK: %s on %s", apk_path, self.device_id)
        self.run("install", "-r", "-g", apk_path, timeout=120)

    def uninstall_app(self, package: str) -> None:
        """
        Uninstall an app from the device by its package name.

        Does **not** raise if the package is not installed (``check=False``).

        Args:
            package: Fully-qualified Android package name.
        """
        logger.info("Uninstalling %s from %s", package, self.device_id)
        self.run("uninstall", package, check=False)

    def start_app(self, package: str, activity: str) -> None:
        """
        Launch an activity via ``am start``.

        Args:
            package:  Fully-qualified package name.
            activity: Fully-qualified activity class name (with or without the
                      leading dot).  Combined as ``package/activity``.

        Raises:
            ADBError: If the ``am start`` command fails.
        """
        self.shell(f"am start -n {package}/{activity}")

    def stop_app(self, package: str) -> None:
        """
        Force-stop an application.

        Args:
            package: Fully-qualified package name.

        Raises:
            ADBError: If the ``am force-stop`` command fails.
        """
        self.shell(f"am force-stop {package}")

    def clear_app_data(self, package: str) -> None:
        """
        Clear all data and cache for an application (equivalent to
        *Settings → App Info → Clear Data*).

        Args:
            package: Fully-qualified package name.

        Raises:
            ADBError: If the ``pm clear`` command fails.
        """
        self.shell(f"pm clear {package}")

    def grant_permission(self, package: str, permission: str) -> None:
        """
        Grant a single runtime permission to an app.

        Errors are suppressed (``check=False``) because some permissions may
        not be applicable on all Android versions.

        Args:
            package:    Fully-qualified package name.
            permission: Android permission string
                        (e.g. ``"android.permission.CAMERA"``).
        """
        self.shell(f"pm grant {package} {permission}", check=False)

    def grant_all_permissions(self, package: str) -> None:
        """
        Grant a set of commonly required runtime permissions to an app.

        The permissions granted include storage, camera, audio, location,
        contacts, phone state, and SMS access.  Individual failures are
        silently ignored.

        Args:
            package: Fully-qualified package name.
        """
        permissions = [
            "android.permission.READ_EXTERNAL_STORAGE",
            "android.permission.WRITE_EXTERNAL_STORAGE",
            "android.permission.CAMERA",
            "android.permission.RECORD_AUDIO",
            "android.permission.ACCESS_FINE_LOCATION",
            "android.permission.ACCESS_COARSE_LOCATION",
            "android.permission.READ_CONTACTS",
            "android.permission.WRITE_CONTACTS",
            "android.permission.READ_PHONE_STATE",
            "android.permission.SEND_SMS",
            "android.permission.READ_SMS",
        ]
        for perm in permissions:
            self.grant_permission(package, perm)

    # ─── File operations ───────────────────────────────────────────────────────

    def push(self, local: str, remote: str) -> None:
        """
        Push a local file or directory to the device.

        Args:
            local:  Path on the host machine.
            remote: Destination path on the device.

        Raises:
            ADBError: If the push fails.
        """
        self.run("push", local, remote)

    def pull(self, remote: str, local: str) -> None:
        """
        Pull a file or directory from the device to the host.

        Args:
            remote: Path on the device.
            local:  Destination path on the host machine.

        Raises:
            ADBError: If the pull fails.
        """
        self.run("pull", remote, local)

    def remove(self, remote_path: str) -> None:
        """
        Remove a file or directory from the device.

        Uses ``rm -rf``; errors are suppressed (``check=False``).

        Args:
            remote_path: Path on the device to remove.
        """
        self.shell(f"rm -rf {remote_path}", check=False)

    # ─── Logcat ────────────────────────────────────────────────────────────────

    def clear_logcat(self) -> None:
        """
        Clear the logcat ring buffer on the device.

        Useful before starting a test run to remove stale log entries.
        Errors are suppressed.
        """
        self.shell("logcat -c", check=False)

    def get_logcat_snapshot(self, lines: int = 500) -> str:
        """
        Capture a snapshot of the current logcat buffer and return it as a
        string.

        Args:
            lines: Maximum number of recent log lines to retrieve.

        Returns:
            A string containing the log output, or an empty string on failure.
        """
        try:
            return self.shell(
                f"logcat -d -t {lines}", timeout=10, check=False
            )
        except Exception:
            return ""

    # ─── Performance ───────────────────────────────────────────────────────────

    def get_cpu_usage(self, package: str) -> float:
        """
        Return the current CPU usage percentage for the given package.

        Reads ``dumpsys cpuinfo`` and searches for a line containing *package*
        with a percentage value.  Falls back to ``0.0`` on any failure or when
        the process is not found.

        Args:
            package: Fully-qualified package name.

        Returns:
            CPU usage as a float percentage (e.g. ``12.5``), or ``0.0``.
        """
        try:
            out = self.shell("dumpsys cpuinfo", timeout=10, check=False)
            for line in out.split("\n"):
                if package in line:
                    match = re.search(r"([\d.]+)%", line)
                    if match:
                        return float(match.group(1))
        except Exception:
            pass
        return 0.0

    def get_memory_usage(self, package: str) -> dict[str, int]:
        """
        Return memory usage statistics for the given package in kilobytes.

        Reads ``dumpsys meminfo <package>`` and extracts:

        * ``pss_total``     — Proportional Set Size total (KB)
        * ``private_dirty`` — Private dirty pages (KB)
        * ``heap_size``     — Dalvik/ART heap size (KB)

        All values default to ``0`` on failure.

        Args:
            package: Fully-qualified package name.

        Returns:
            Dictionary with keys ``pss_total``, ``private_dirty``,
            ``heap_size``, all as integers (KB).
        """
        result: dict[str, int] = {
            "pss_total": 0,
            "private_dirty": 0,
            "heap_size": 0,
        }
        try:
            out = self.shell(
                f"dumpsys meminfo {package}", timeout=10, check=False
            )
            match = re.search(r"TOTAL\s+(\d+)", out)
            if match:
                result["pss_total"] = int(match.group(1))
            match = re.search(r"Private Dirty\s+(\d+)", out)
            if match:
                result["private_dirty"] = int(match.group(1))
            match = re.search(r"Heap Size:\s+(\d+)", out)
            if match:
                result["heap_size"] = int(match.group(1))
        except Exception:
            pass
        return result

    def get_pid(self, package: str) -> str | None:
        """
        Return the PID of a running process identified by *package*.

        Uses the ``pidof`` command on the device.

        Args:
            package: Fully-qualified package name (or process name).

        Returns:
            The PID as a string (e.g. ``"12345"``), or ``None`` if the
            process is not running or the command fails.
        """
        try:
            out = self.shell(f"pidof {package}", check=False)
            pid = out.strip()
            return pid if pid else None
        except Exception:
            return None
