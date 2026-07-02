"""
Environment checker for the automation framework.
Runs pre-flight checks before any test execution.

Checks:
- Python version >= 3.10
- pytest importable
- Node.js executable
- Appium server executable and version
- Appium uiautomator2 driver installed
- ADB executable and server
- ANDROID_HOME or ANDROID_SDK_ROOT env var
- At least one device connected (or specified device exists)
- App package/activity configured
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from automation_framework.lib.utils.time import now_iso

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """Result of a single environment pre-flight check.

    Attributes:
        name: Human-readable name of the check.
        passed: Whether the check passed.
        detail: Additional detail string (e.g. version, path, found items).
        hint: Actionable hint shown to the user when the check fails.
    """

    name: str
    passed: bool
    detail: str = ""
    hint: str = ""


@dataclass
class EnvironmentCheckReport:
    """Aggregated report produced by :class:`EnvironmentChecker`.

    Attributes:
        timestamp: ISO-8601 timestamp at the time the report was created.
        overall_passed: ``True`` only when every individual check passed.
        checks: Ordered list of :class:`CheckResult` objects.
        available_devices: Online ADB device serial numbers discovered during
            the device-connectivity check.
    """

    timestamp: str = field(default_factory=now_iso)
    overall_passed: bool = False
    checks: list[CheckResult] = field(default_factory=list)
    available_devices: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize the report to a plain dictionary suitable for JSON output."""
        return {
            "timestamp": self.timestamp,
            "overall_passed": self.overall_passed,
            "available_devices": self.available_devices,
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "detail": c.detail,
                    "hint": c.hint,
                }
                for c in self.checks
            ],
        }


class EnvironmentChecker:
    """Runs all pre-flight environment checks.

    Each check is a private method that returns a :class:`CheckResult`.
    Checks are executed sequentially in :meth:`run` and their results are
    collected into an :class:`EnvironmentCheckReport`.

    Example::

        checker = EnvironmentChecker(config, devices=["emulator-5554"])
        report = checker.run()
        if not report.overall_passed:
            sys.exit(2)
    """

    def __init__(self, config: Any, devices: list[str] | None = None) -> None:
        """Initialise the checker.

        Args:
            config: Framework config object exposing ``.app``, ``.appium``, etc.
            devices: Explicit device serial numbers to verify.  When ``None``
                the checker auto-discovers all online ADB devices.
        """
        self._config = config
        self._devices = devices
        self._report = EnvironmentCheckReport()

    # ─────────────────────────────────── public ───────────────────────────────

    def run(self) -> EnvironmentCheckReport:
        """Execute every pre-flight check and return the aggregated report.

        Each check function is called in order.  Results are logged at INFO
        (pass) or ERROR (fail) level.  Failure hints are emitted as WARNING.

        Returns:
            A fully populated :class:`EnvironmentCheckReport`.
        """
        checks = [
            self._check_python,
            self._check_pytest,
            self._check_nodejs,
            self._check_appium,
            self._check_appium_driver,
            self._check_adb,
            self._check_android_sdk,
            self._check_devices,
            self._check_app_config,
        ]

        for check_fn in checks:
            result = check_fn()
            self._report.checks.append(result)
            status = "✓" if result.passed else "✗"
            level = logging.INFO if result.passed else logging.ERROR
            logger.log(
                level,
                "[env-check] %s %-30s %s",
                status,
                result.name,
                result.detail,
            )
            if not result.passed and result.hint:
                logger.warning("  → Hint: %s", result.hint)

        self._report.overall_passed = all(c.passed for c in self._report.checks)
        return self._report

    # ─────────────────────────────────── checks ───────────────────────────────

    def _check_python(self) -> CheckResult:
        """Verify that the running Python interpreter is >= 3.10."""
        version = sys.version_info
        passed = version >= (3, 10)
        detail = f"{version.major}.{version.minor}.{version.micro}"
        hint = (
            ""
            if passed
            else "Please upgrade to Python 3.10+. Visit https://www.python.org/downloads/"
        )
        return CheckResult("Python version", passed, detail, hint)

    def _check_pytest(self) -> CheckResult:
        """Verify that pytest is importable in the current environment."""
        try:
            import pytest  # noqa: PLC0415

            return CheckResult("pytest", True, pytest.__version__)
        except ImportError:
            return CheckResult(
                "pytest",
                False,
                "Not importable",
                "Run: pip install pytest",
            )

    def _check_nodejs(self) -> CheckResult:
        """Verify that the ``node`` executable is on PATH and returns a version."""
        node = shutil.which("node")
        if not node:
            return CheckResult(
                "Node.js",
                False,
                "node executable not found",
                "Install Node.js >= 14 from https://nodejs.org/",
            )
        try:
            result = subprocess.run(
                ["node", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            version = result.stdout.strip()
            return CheckResult("Node.js", True, version)
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                "Node.js",
                False,
                str(exc),
                "Check your Node.js installation",
            )

    def _check_appium(self) -> CheckResult:
        """Verify that the Appium CLI is on PATH and returns a version string."""
        appium = shutil.which("appium")
        if not appium:
            return CheckResult(
                "Appium Server",
                False,
                "appium command not found",
                "Run: npm install -g appium",
            )
        try:
            result = subprocess.run(
                ["appium", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            version = result.stdout.strip() or result.stderr.strip()
            return CheckResult("Appium Server", True, f"v{version}")
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                "Appium Server",
                False,
                str(exc),
                "Run: npm install -g appium",
            )

    def _check_appium_driver(self) -> CheckResult:
        """Verify that the Appium ``uiautomator2`` driver is installed."""
        try:
            result = subprocess.run(
                ["appium", "driver", "list", "--installed"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            output = result.stdout + result.stderr
            if "uiautomator2" in output.lower():
                return CheckResult("Appium uiautomator2 driver", True, "installed")
            return CheckResult(
                "Appium uiautomator2 driver",
                False,
                "uiautomator2 not found in installed drivers",
                "Run: appium driver install uiautomator2",
            )
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                "Appium uiautomator2 driver",
                False,
                str(exc),
                "Run: appium driver install uiautomator2",
            )

    def _check_adb(self) -> CheckResult:
        """Verify that ``adb`` is on PATH and can start its server."""
        adb = shutil.which("adb")
        if not adb:
            return CheckResult(
                "ADB",
                False,
                "adb command not found",
                "Install Android SDK Platform Tools and add to PATH",
            )
        try:
            # Ensure the ADB server is running before any subsequent checks.
            subprocess.run(["adb", "start-server"], capture_output=True, timeout=10)
            result = subprocess.run(
                ["adb", "version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            version_line = result.stdout.strip().split("\n")[0]
            return CheckResult("ADB", True, version_line)
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                "ADB",
                False,
                str(exc),
                "Install Android SDK Platform Tools: "
                "https://developer.android.com/tools/releases/platform-tools",
            )

    def _check_android_sdk(self) -> CheckResult:
        """Verify that ``ANDROID_HOME`` or ``ANDROID_SDK_ROOT`` is set and valid."""
        sdk_root = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
        if not sdk_root:
            return CheckResult(
                "Android SDK",
                False,
                "ANDROID_HOME and ANDROID_SDK_ROOT not set",
                "Set ANDROID_HOME to your Android SDK directory (e.g., ~/Library/Android/sdk)",
            )
        sdk_path = Path(sdk_root)
        if not sdk_path.exists():
            return CheckResult(
                "Android SDK",
                False,
                f"Path does not exist: {sdk_root}",
                "Ensure ANDROID_HOME points to a valid Android SDK directory",
            )
        return CheckResult("Android SDK", True, sdk_root)

    def _check_devices(self) -> CheckResult:
        """Check device connectivity via ``adb devices``.

        Populates :attr:`EnvironmentCheckReport.available_devices` with the
        serial numbers of all *online* devices.  When explicit device serials
        were provided at construction time, verifies that each one appears in
        the online list.
        """
        try:
            result = subprocess.run(
                ["adb", "devices"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            # First line is the header "List of devices attached"
            lines = result.stdout.strip().split("\n")[1:]
            online: list[str] = []

            for line in lines:
                line = line.strip()
                if "\t" not in line:
                    continue
                serial, state = line.split("\t", 1)
                serial = serial.strip()
                state = state.strip()
                if state == "device":
                    online.append(serial)
                elif state == "offline":
                    logger.warning("Device %s is OFFLINE — skipping", serial)
                elif state == "unauthorized":
                    logger.warning(
                        "Device %s is UNAUTHORIZED — please accept debug prompt on device",
                        serial,
                    )

            self._report.available_devices = online

            if self._devices:
                missing = [d for d in self._devices if d not in online]
                if missing:
                    return CheckResult(
                        "Device connection",
                        False,
                        f"Specified device(s) not online: {', '.join(missing)}",
                        "Check USB connection or run: adb devices",
                    )
                return CheckResult(
                    "Device connection",
                    True,
                    f"{len(self._devices)} specified device(s) online: "
                    f"{', '.join(self._devices)}",
                )
            else:
                if not online:
                    return CheckResult(
                        "Device connection",
                        False,
                        "No online devices found",
                        "Connect an Android device or start an emulator, "
                        "then run: adb devices",
                    )
                return CheckResult(
                    "Device connection",
                    True,
                    f"{len(online)} device(s) online: {', '.join(online)}",
                )
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                "Device connection",
                False,
                str(exc),
                "Check ADB installation",
            )

    def _check_app_config(self) -> CheckResult:
        """Verify that the minimum app configuration values are present.

        Checks:
        * ``app.package`` is non-empty.
        * ``app.activity`` is non-empty.
        * ``app.install_path``, if set, points to an existing file.
        """
        pkg: str = getattr(self._config.app, "package", "") or ""
        act: str = getattr(self._config.app, "activity", "") or ""
        install_path: str = getattr(self._config.app, "install_path", "") or ""

        if not pkg:
            return CheckResult(
                "App configuration",
                False,
                "app.package is not configured",
                "Set app.package in config/default.yaml or use --app-package",
            )
        if not act:
            return CheckResult(
                "App configuration",
                False,
                "app.activity is not configured",
                "Set app.activity in config/default.yaml or use --app-activity",
            )
        if install_path and not Path(install_path).exists():
            return CheckResult(
                "App configuration",
                False,
                f"install_path does not exist: {install_path}",
                "Check the APK path in config",
            )

        detail = f"package={pkg}, activity={act}"
        if install_path:
            detail += f", install_path={install_path}"
        return CheckResult("App configuration", True, detail)
