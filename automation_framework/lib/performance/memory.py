"""Memory usage collector for Android apps."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from automation_framework.lib.adb.client import ADBClient
from automation_framework.lib.performance.base import PerformanceCollector

logger = logging.getLogger(__name__)


class MemoryCollector(PerformanceCollector):
    """
    Collects PSS memory usage (KB) for the target app package.
    Writes samples to memory.jsonl.
    """

    def __init__(
        self,
        adb: ADBClient,
        device_id: str,
        app_package: str,
        output_path: Path,
        interval: float = 2.0,
    ):
        super().__init__(device_id, app_package, output_path, interval)
        self._adb = adb

    @property
    def metric_name(self) -> str:
        return "memory"

    def collect_sample(self) -> dict[str, Any] | None:
        try:
            mem = self._adb.get_memory_usage(self.app_package)
            return {"value": mem.get("pss_total", 0), "unit": "KB", **mem}
        except Exception as e:
            logger.debug("Memory sample error: %s", e)
            return None
