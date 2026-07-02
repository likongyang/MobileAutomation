"""
Device worker - manages the full test execution lifecycle for a single device.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from automation_framework.lib.adb.client import ADBClient
from automation_framework.lib.appium.ports import DevicePorts, allocate_ports, release_ports
from automation_framework.lib.appium.server import AppiumServer, AppiumServerError
from automation_framework.lib.device.info import collect_device_info
from automation_framework.lib.log.logcat import LogcatCollector
from automation_framework.lib.performance.cpu import CPUCollector
from automation_framework.lib.performance.memory import MemoryCollector
from automation_framework.lib.report.schema import (
    CaseReport, CaseStatus, DeviceReport, DeviceStatus
)
from automation_framework.lib.utils.file import write_json
from automation_framework.lib.utils.time import now_iso

if TYPE_CHECKING:
    from automation_framework.lib.artifacts.manager import ArtifactsManager
    from automation_framework.lib.utils.config_loader import Config

logger = logging.getLogger(__name__)


class DeviceWorker:
    """
    Manages the complete test execution lifecycle for a single Android device.

    Responsibilities:
    - Collect device info
    - Start Appium server (dedicated port)
    - Start logcat (if enabled)
    - Start performance collectors (if enabled)
    - Launch pytest subprocess with device context injected via env vars
    - Monitor for device disconnect
    - Collect and return DeviceReport

    Args:
        device_id:     ADB serial of the target device.
        device_index:  Zero-based index used for deterministic port allocation.
        config:        Loaded framework :class:`Config` object.
        artifacts:     :class:`ArtifactsManager` instance for the current run.
    """

    def __init__(
        self,
        device_id: str,
        device_index: int,
        config: "Config",
        artifacts: "ArtifactsManager",
    ) -> None:
        self.device_id = device_id
        self.device_index = device_index
        self._config = config
        self._artifacts = artifacts

        self._report = DeviceReport(
            device_id=device_id,
            run_id=artifacts.run_id,
            status=DeviceStatus.RUNNING,
        )
        self._ports: DevicePorts | None = None
        self._appium_server: AppiumServer | None = None
        self._logcat: LogcatCollector | None = None
        self._cpu_collector: CPUCollector | None = None
        self._memory_collector: MemoryCollector | None = None
        self._disconnect_event = threading.Event()
        self._monitor_thread: threading.Thread | None = None

    # ─── Public entry point ───────────────────────────────────────────────────

    def run(self) -> DeviceReport:
        """
        Execute all tests on this device and return a completed :class:`DeviceReport`.

        The method runs sequentially through these phases:

        1. Collect device hardware / software info.
        2. Allocate Appium / system / mjpeg ports.
        3. Start the Appium server.
        4. Optionally start logcat collection.
        5. Optionally start CPU & memory performance collectors.
        6. Start the background device-disconnect monitor.
        7. Run pytest as a subprocess.
        8. Cleanup (stop collectors, release ports, etc.).

        Returns:
            A :class:`DeviceReport` with ``status``, ``cases``, and paths to
            all collected artifacts.
        """
        logger.info("[%s] Worker starting", self.device_id)
        adb = ADBClient(self.device_id, timeout=30)

        try:
            # 1. Collect device info
            self._collect_device_info(adb)

            # 2. Allocate ports
            self._ports = allocate_ports(
                self.device_id,
                appium_base=self._config.appium.base_port,
                system_base=self._config.appium.system_base_port,
                mjpeg_base=self._config.appium.mjpeg_base_port,
                device_index=self.device_index,
            )
            write_json(
                self._artifacts.appium_ports_path(self.device_id),
                self._ports.to_dict(),
            )

            # 3. Start Appium server
            self._start_appium()

            # 4. Start logcat (if enabled)
            self._start_logcat(adb)

            # 5. Start performance collectors (if enabled)
            self._start_performance(adb)

            # 6. Start disconnect monitor
            self._start_monitor(adb)

            # 7. Run pytest subprocess
            self._run_pytest()

        except AppiumServerError as e:
            logger.error("[%s] Appium Server failed: %s", self.device_id, e)
            self._report.status = DeviceStatus.ERROR
        except Exception as e:
            logger.error("[%s] Worker error: %s", self.device_id, e, exc_info=True)
            self._report.status = DeviceStatus.ERROR
        finally:
            self._cleanup()

        if self._report.status == DeviceStatus.RUNNING:
            self._report.status = DeviceStatus.FINISHED

        logger.info(
            "[%s] Worker finished: status=%s, cases=%d",
            self.device_id,
            self._report.status,
            len(self._report.cases),
        )
        return self._report

    # ─── Phases ───────────────────────────────────────────────────────────────

    def _collect_device_info(self, adb: ADBClient) -> None:
        """
        Query ADB for device hardware/software information and persist it as JSON.

        Populates ``self._report.device_info_path`` with the relative path to
        the saved JSON file.
        """
        info = collect_device_info(adb, self._config.app.package)
        info_path = self._artifacts.device_info_path(self.device_id)
        write_json(info_path, info.to_dict())
        self._report.device_info_path = self._artifacts.rel(info_path)
        logger.info(
            "[%s] Device info: %s %s Android %s",
            self.device_id,
            info.brand,
            info.model,
            info.android_version,
        )

    def _start_appium(self) -> None:
        """
        Instantiate and start the :class:`AppiumServer` for this device.

        Raises:
            AppiumServerError: If the server fails to reach the ready state
                within ``startup_timeout_seconds``.
        """
        assert self._ports is not None, "Ports must be allocated before starting Appium"

        log_path = self._artifacts.appium_server_log_path(self.device_id)
        self._report.appium_log_path = self._artifacts.rel(log_path)

        self._appium_server = AppiumServer(
            ports=self._ports,
            log_path=log_path,
            host=self._config.appium.host,
            startup_timeout=self._config.appium.startup_timeout_seconds,
        )
        self._appium_server.start()
        logger.info(
            "[%s] Appium server started at %s",
            self.device_id,
            self._appium_server.url,
        )

    def _start_logcat(self, adb: ADBClient) -> None:  # noqa: ARG002
        """
        Start background logcat collection if enabled in config.

        The raw log is written to the path provided by
        :meth:`ArtifactsManager.logcat_raw_path`.
        """
        if not self._config.logcat.enabled:
            return

        self._logcat = LogcatCollector(
            device_id=self.device_id,
            raw_log_path=self._artifacts.logcat_raw_path(self.device_id),
            package_filter=self._config.logcat.filters.package,
            keyword_filters=self._config.logcat.filters.keywords,
        )
        self._logcat.start(clear_first=self._config.logcat.clear_before_run)
        logger.debug("[%s] Logcat collection started", self.device_id)

    def _start_performance(self, adb: ADBClient) -> None:
        """
        Start CPU and/or memory performance collectors if enabled in config.

        Each enabled collector runs in its own daemon thread at the configured
        polling interval and appends JSONL records to the appropriate artifact
        path.
        """
        if not self._config.performance.enabled:
            return

        metrics: list[str] = self._config.performance.metrics
        interval: float = self._config.performance.interval_seconds

        if "cpu" in metrics:
            self._cpu_collector = CPUCollector(
                adb=adb,
                device_id=self.device_id,
                app_package=self._config.app.package,
                output_path=self._artifacts.cpu_jsonl_path(self.device_id),
                interval=interval,
            )
            self._cpu_collector.start()
            logger.debug("[%s] CPU collector started (interval=%.1fs)", self.device_id, interval)

        if "memory" in metrics:
            self._memory_collector = MemoryCollector(
                adb=adb,
                device_id=self.device_id,
                app_package=self._config.app.package,
                output_path=self._artifacts.memory_jsonl_path(self.device_id),
                interval=interval,
            )
            self._memory_collector.start()
            logger.debug(
                "[%s] Memory collector started (interval=%.1fs)", self.device_id, interval
            )

    def _start_monitor(self, adb: ADBClient) -> None:
        """
        Start a background daemon thread that polls :meth:`ADBClient.is_online`.

        If the device remains offline for longer than
        ``execution.device_offline_timeout_seconds`` the worker's report status
        is set to :attr:`DeviceStatus.DISCONNECTED` and the disconnect event is
        signalled so that cleanup can proceed immediately.
        """
        timeout: int = self._config.execution.device_offline_timeout_seconds

        def _monitor_loop() -> None:
            offline_since: float | None = None

            while not self._disconnect_event.is_set():
                if not adb.is_online():
                    if offline_since is None:
                        offline_since = time.monotonic()
                        logger.warning(
                            "[%s] Device went offline (offline_timeout=%ds)",
                            self.device_id,
                            timeout,
                        )
                    elif time.monotonic() - offline_since > timeout:
                        logger.error(
                            "[%s] Device offline for >%ds – marking as DISCONNECTED",
                            self.device_id,
                            timeout,
                        )
                        self._report.status = DeviceStatus.DISCONNECTED
                        self._disconnect_event.set()
                        return
                else:
                    if offline_since is not None:
                        logger.info("[%s] Device came back online", self.device_id)
                    offline_since = None

                time.sleep(5)

        self._monitor_thread = threading.Thread(
            target=_monitor_loop,
            name=f"monitor-{self.device_id}",
            daemon=True,
        )
        self._monitor_thread.start()
        logger.debug(
            "[%s] Disconnect monitor started (timeout=%ds)", self.device_id, timeout
        )

    def _run_pytest(self) -> None:
        """
        Launch pytest as a subprocess with device context injected via environment
        variables and wait for it to complete.

        Environment variables injected (prefixed ``AF_``):

        ================== ===============================================
        ``AF_DEVICE_ID``   ADB serial of the target device.
        ``AF_APPIUM_URL``  Full URL of the Appium server.
        ``AF_APPIUM_PORT`` Appium HTTP listen port.
        ``AF_SYSTEM_PORT`` UIAutomator2 system port.
        ``AF_RUN_ID``      Unique identifier for this run.
        ``AF_ARTIFACTS_DIR`` Absolute path to the run artifact directory.
        ``AF_APP_PACKAGE``   Android application package name.
        ``AF_APP_ACTIVITY``  Android application launch activity.
        ================== ===============================================

        After pytest exits, :meth:`_load_pytest_results` is called to parse
        the JSON result file (or fall back to JUnit XML).
        """
        assert self._ports is not None, "Ports must be set before running pytest"

        env = os.environ.copy()
        env.update(
            {
                "AF_DEVICE_ID": self.device_id,
                "AF_APPIUM_URL": self._appium_server.url if self._appium_server else "",
                "AF_APPIUM_PORT": str(self._ports.appium_port),
                "AF_SYSTEM_PORT": str(self._ports.system_port),
                "AF_RUN_ID": self._artifacts.run_id,
                "AF_ARTIFACTS_DIR": str(self._artifacts.run_dir),
                "AF_APP_PACKAGE": self._config.app.package,
                "AF_APP_ACTIVITY": self._config.app.activity,
            }
        )

        result_json = self._artifacts.device_dir(self.device_id) / "pytest_result.json"
        junit_xml_path = self._artifacts.junit_xml_path()

        cmd: list[str] = [
            sys.executable,
            "-m",
            "pytest",
            self._config.execution.tests,
            "-v",
            "--tb=short",
            f"--junitxml={junit_xml_path}",
            f"--result-json={result_json}",
        ]

        if self._config.execution.markers:
            cmd += ["-m", self._config.execution.markers]

        if self._config.execution.keyword:
            cmd += ["-k", self._config.execution.keyword]

        if self._config.execution.reruns > 0:
            cmd += ["--reruns", str(self._config.execution.reruns)]

        logger.info("[%s] Launching pytest: %s", self.device_id, " ".join(cmd))

        proc = subprocess.run(
            cmd,
            env=env,
            cwd=str(Path.cwd()),
            capture_output=False,
        )

        logger.info(
            "[%s] pytest exited with return code %d",
            self.device_id,
            proc.returncode,
        )

        self._load_pytest_results(result_json)

    # ─── Result parsing ───────────────────────────────────────────────────────

    def _load_pytest_results(self, result_json: Path) -> None:
        """
        Parse the pytest JSON result file and populate :attr:`DeviceReport.cases`.

        Falls back to :meth:`_load_junit_results` when the JSON file is absent.

        Args:
            result_json: Path to the ``pytest_result.json`` file written by
                         pytest during the test run.
        """
        if not result_json.exists():
            logger.debug(
                "[%s] pytest JSON result not found – falling back to JUnit XML",
                self.device_id,
            )
            self._load_junit_results()
            return

        try:
            data: dict = json.loads(result_json.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(
                "[%s] Failed to read pytest JSON result: %s – falling back to JUnit XML",
                self.device_id,
                exc,
            )
            self._load_junit_results()
            return

        _status_map: dict[str, CaseStatus] = {
            "passed": CaseStatus.PASSED,
            "failed": CaseStatus.FAILED,
            "error": CaseStatus.ERROR,
            "skipped": CaseStatus.SKIPPED,
        }

        for test in data.get("tests", []):
            nodeid: str = test.get("nodeid", "")
            outcome: str = test.get("outcome", "unknown")
            status: CaseStatus = _status_map.get(outcome, CaseStatus.UNKNOWN)

            call_info = test.get("call")
            message: str = ""
            if isinstance(call_info, dict):
                message = call_info.get("longrepr", "") or ""

            self._report.cases.append(
                CaseReport(
                    nodeid=nodeid,
                    device_id=self.device_id,
                    status=status,
                    duration_seconds=float(test.get("duration", 0.0)),
                    message=message,
                )
            )

        logger.debug(
            "[%s] Loaded %d test cases from pytest JSON",
            self.device_id,
            len(self._report.cases),
        )

    def _load_junit_results(self) -> None:
        """
        Fallback result parser that reads the JUnit XML produced by pytest's
        ``--junitxml`` argument.

        Populates :attr:`DeviceReport.cases` from ``<testcase>`` elements.
        Called when the richer JSON result file is unavailable.
        """
        import xml.etree.ElementTree as ET

        junit_path = self._artifacts.junit_xml_path()
        if not junit_path.exists():
            logger.warning(
                "[%s] Neither pytest JSON nor JUnit XML found – no results collected",
                self.device_id,
            )
            return

        try:
            tree = ET.parse(junit_path)
            root = tree.getroot()
        except Exception as exc:
            logger.warning("[%s] Failed to parse JUnit XML: %s", self.device_id, exc)
            return

        for testsuite in root.iter("testsuite"):
            for testcase in testsuite.findall("testcase"):
                class_name: str = testcase.get("classname", "")
                name: str = testcase.get("name", "")
                nodeid = (
                    f"{class_name.replace('.', '/')}.py::{name}"
                    if class_name
                    else name
                )
                duration = float(testcase.get("time", 0))

                failure = testcase.find("failure")
                error = testcase.find("error")
                skipped = testcase.find("skipped")

                if failure is not None:
                    status = CaseStatus.FAILED
                    message = failure.get("message", "")
                elif error is not None:
                    status = CaseStatus.ERROR
                    message = error.get("message", "")
                elif skipped is not None:
                    status = CaseStatus.SKIPPED
                    message = skipped.get("message", "")
                else:
                    status = CaseStatus.PASSED
                    message = ""

                self._report.cases.append(
                    CaseReport(
                        nodeid=nodeid,
                        device_id=self.device_id,
                        status=status,
                        duration_seconds=duration,
                        message=message,
                    )
                )

        logger.debug(
            "[%s] Loaded %d test cases from JUnit XML",
            self.device_id,
            len(self._report.cases),
        )

    # ─── Cleanup ──────────────────────────────────────────────────────────────

    def _cleanup(self) -> None:
        """
        Gracefully stop all background processes and release resources.

        Cleanup order:

        1. Signal the disconnect monitor to stop.
        2. Stop CPU collector (if started) and record artifact path.
        3. Stop memory collector (if started) and record artifact path.
        4. Stop logcat collector (if started).
        5. Stop Appium server (if started).
        6. Release allocated ports.
        7. Join the monitor thread (timeout 5 s).

        Exceptions raised by individual stop calls are caught and logged so
        that the remaining cleanup steps always execute.
        """
        logger.debug("[%s] Beginning cleanup", self.device_id)

        # Signal the monitor to exit its polling loop
        self._disconnect_event.set()

        # ── Performance collectors ──────────────────────────────────────────
        if self._cpu_collector is not None:
            try:
                self._cpu_collector.stop()
            except Exception as exc:
                logger.debug("[%s] Error stopping CPU collector: %s", self.device_id, exc)
            self._report.performance["cpu"] = self._artifacts.rel(
                self._artifacts.cpu_jsonl_path(self.device_id)
            )

        if self._memory_collector is not None:
            try:
                self._memory_collector.stop()
            except Exception as exc:
                logger.debug("[%s] Error stopping memory collector: %s", self.device_id, exc)
            self._report.performance["memory"] = self._artifacts.rel(
                self._artifacts.memory_jsonl_path(self.device_id)
            )

        # ── Logcat ──────────────────────────────────────────────────────────
        if self._logcat is not None:
            try:
                self._logcat.stop()
            except Exception as exc:
                logger.debug("[%s] Error stopping logcat: %s", self.device_id, exc)

        # ── Appium server ────────────────────────────────────────────────────
        if self._appium_server is not None:
            try:
                self._appium_server.stop()
            except Exception as exc:
                logger.debug("[%s] Error stopping Appium server: %s", self.device_id, exc)

        # ── Ports ────────────────────────────────────────────────────────────
        if self._ports is not None:
            try:
                release_ports(self._ports)
            except Exception as exc:
                logger.debug("[%s] Error releasing ports: %s", self.device_id, exc)
            finally:
                self._ports = None

        # ── Monitor thread ────────────────────────────────────────────────────
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=5)
            if self._monitor_thread.is_alive():
                logger.warning(
                    "[%s] Monitor thread did not exit within 5 s", self.device_id
                )
            self._monitor_thread = None

        logger.info("[%s] Cleanup complete", self.device_id)
