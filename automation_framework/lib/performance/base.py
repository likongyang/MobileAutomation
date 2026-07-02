"""Performance collector base class."""
from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from automation_framework.lib.utils.file import append_jsonl
from automation_framework.lib.utils.time import current_timestamp

logger = logging.getLogger(__name__)


class PerformanceCollector(ABC):
    """
    Base class for all performance metric collectors.
    Runs in a background thread, polling at a configurable interval.
    """

    def __init__(
        self,
        device_id: str,
        app_package: str,
        output_path: Path,
        interval: float = 2.0,
    ):
        self.device_id = device_id
        self.app_package = app_package
        self.output_path = output_path
        self.interval = interval
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._current_case: str | None = None
        self._case_output_path: Path | None = None

    @property
    @abstractmethod
    def metric_name(self) -> str:
        """Name of the metric (e.g., 'cpu', 'memory')."""
        ...

    @abstractmethod
    def collect_sample(self) -> dict[str, Any] | None:
        """
        Collect one sample of data.
        Return a dict (will be written to JSONL) or None to skip.
        """
        ...

    def start(self) -> None:
        """Start background collection thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"perf-{self.metric_name}-{self.device_id}", daemon=True
        )
        self._thread.start()
        logger.info(
            "[perf] Started %s collector for %s", self.metric_name, self.device_id
        )

    def stop(self) -> None:
        """Stop the background collection thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info(
            "[perf] Stopped %s collector for %s", self.metric_name, self.device_id
        )

    def set_case_context(self, case_nodeid: str | None, case_output: Path | None) -> None:
        """Update the current test case context for per-case output."""
        self._current_case = case_nodeid
        self._case_output_path = case_output

    def _run(self) -> None:
        """Background polling loop."""
        while not self._stop_event.is_set():
            try:
                sample = self.collect_sample()
                if sample:
                    record = {
                        "device_id": self.device_id,
                        "case_id": self._current_case,
                        "metric": self.metric_name,
                        "timestamp": current_timestamp(),
                        **sample,
                    }
                    append_jsonl(self.output_path, record)
                    if self._case_output_path:
                        append_jsonl(self._case_output_path, record)
            except Exception as e:
                logger.debug("[perf] %s sample error: %s", self.metric_name, e)
            self._stop_event.wait(self.interval)
