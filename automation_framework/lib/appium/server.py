"""
Appium Server lifecycle management.
Starts, monitors and stops an Appium server process for a single device.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import IO

from automation_framework.lib.appium.health import wait_for_appium
from automation_framework.lib.appium.ports import DevicePorts

logger = logging.getLogger(__name__)


class AppiumServerError(Exception):
    """Raised when Appium server fails to start or crashes."""


class AppiumServer:
    """
    Manages one Appium Server process for a single device.
    """

    def __init__(
        self,
        ports: DevicePorts,
        log_path: Path,
        host: str = "127.0.0.1",
        startup_timeout: float = 30.0,
    ):
        self.ports = ports
        self.log_path = log_path
        self.host = host
        self.startup_timeout = startup_timeout
        self._process: subprocess.Popen | None = None
        self._log_file: IO | None = None
        self._running = False

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.ports.appium_port}"

    def start(self) -> None:
        """
        Start the Appium Server process and wait for it to become healthy.
        Raises AppiumServerError on failure.
        """
        log_path = Path(self.log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = open(log_path, 'w', encoding='utf-8')

        cmd = [
            "appium",
            "--address", self.host,
            "--port", str(self.ports.appium_port),
            "--log-level", "info",
            "--session-override",
        ]

        logger.info(
            "Starting Appium Server for device %s on port %d",
            self.ports.device_id, self.ports.appium_port
        )

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=self._log_file,
                stderr=self._log_file,
                env=os.environ.copy(),
            )
        except FileNotFoundError:
            raise AppiumServerError(
                "appium command not found — run: npm install -g appium"
            )

        # Wait for Appium to be ready
        healthy = wait_for_appium(
            self.host, self.ports.appium_port,
            timeout=self.startup_timeout
        )
        if not healthy:
            self._collect_and_raise()

        self._running = True
        logger.info("Appium Server started: %s", self.url)

    def stop(self) -> None:
        """Stop the Appium Server process and clean up."""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=10)
                logger.info(
                    "Appium Server stopped for device %s",
                    self.ports.device_id
                )
            except subprocess.TimeoutExpired:
                logger.warning("Appium did not terminate — sending SIGKILL")
                self._process.kill()
                self._process.wait()
            except Exception as e:
                logger.warning("Error stopping Appium: %s", e)
            finally:
                self._process = None
                self._running = False

        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

    def is_running(self) -> bool:
        """Return True if the Appium process is still alive."""
        if not self._process:
            return False
        return self._process.poll() is None

    def _collect_and_raise(self) -> None:
        """Read Appium log and raise AppiumServerError."""
        log_tail = ""
        try:
            if self._log_file:
                self._log_file.flush()
            with open(self.log_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
                log_tail = ''.join(lines[-30:])
        except Exception:
            pass
        self.stop()
        raise AppiumServerError(
            f"Appium Server on port {self.ports.appium_port} failed to start.\n"
            f"Last log:\n{log_tail}"
        )

    def __enter__(self) -> "AppiumServer":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()
