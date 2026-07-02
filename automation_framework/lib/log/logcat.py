"""Logcat collector - runs adb logcat in a subprocess and streams to a file."""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)


class LogcatCollector:
    """
    Captures device logcat to a file in a background thread.
    Also supports extracting log windows around case failures.
    """

    def __init__(
        self,
        device_id: str,
        raw_log_path: Path,
        package_filter: str = "",
        keyword_filters: list[str] | None = None,
    ):
        self.device_id = device_id
        self.raw_log_path = raw_log_path
        self.package_filter = package_filter
        self.keyword_filters = keyword_filters or []
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._lines: list[str] = []  # in-memory buffer for window extraction
        self._max_buffer_lines = 10000

    def start(self, clear_first: bool = True) -> None:
        """Start logcat collection."""
        if clear_first:
            try:
                subprocess.run(
                    ["adb", "-s", self.device_id, "shell", "logcat", "-c"],
                    capture_output=True, timeout=5
                )
            except Exception:
                pass

        raw_log_path = Path(self.raw_log_path)
        raw_log_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = ["adb", "-s", self.device_id, "logcat", "-v", "time"]

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                encoding='utf-8',
                errors='replace',
            )
            self._running = True
            self._thread = threading.Thread(
                target=self._stream_to_file,
                args=(raw_log_path,),
                name=f"logcat-{self.device_id}",
                daemon=True,
            )
            self._thread.start()
            logger.info("Logcat started for device %s -> %s", self.device_id, raw_log_path)
        except Exception as e:
            logger.error("Failed to start logcat for %s: %s", self.device_id, e)

    def stop(self) -> None:
        """Stop logcat collection."""
        self._running = False
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("Logcat stopped for device %s", self.device_id)

    def _stream_to_file(self, path: Path) -> None:
        with open(path, 'w', encoding='utf-8', errors='replace') as f:
            proc = self._process
            if proc is None or proc.stdout is None:
                return
            for line in proc.stdout:
                if not self._running:
                    break
                f.write(line)
                f.flush()
                # Keep in-memory buffer
                self._lines.append(line.rstrip())
                if len(self._lines) > self._max_buffer_lines:
                    self._lines.pop(0)

    def get_window(self, lines: int = 200) -> str:
        """Return last N lines from the in-memory logcat buffer."""
        return '\n'.join(self._lines[-lines:])

    def save_failure_window(
        self, output_path: Path, lines: int = 300
    ) -> str:
        """Save a logcat window around failure time to a file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        window = self.get_window(lines)
        output_path.write_text(window, encoding='utf-8')
        logger.info("Failure logcat window saved: %s", output_path)
        return str(output_path)
