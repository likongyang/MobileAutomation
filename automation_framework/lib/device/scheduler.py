"""Device scheduler - runs multiple DeviceWorkers in parallel threads."""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from automation_framework.lib.device.worker import DeviceWorker
from automation_framework.lib.report.aggregator import ReportAggregator
from automation_framework.lib.report.schema import CaseStatus, DeviceStatus
from automation_framework.lib.utils.time import now_iso

if TYPE_CHECKING:
    from automation_framework.lib.artifacts.manager import ArtifactsManager
    from automation_framework.lib.utils.config_loader import Config

logger = logging.getLogger(__name__)


class DeviceScheduler:
    """
    Schedules and runs DeviceWorker instances in parallel threads.
    One thread per device; all run concurrently.
    """

    def __init__(
        self,
        config: Config,
        devices: list[str],
        artifacts: ArtifactsManager,
    ):
        self._config = config
        self._devices = devices
        self._artifacts = artifacts

    def run(self) -> int:
        """
        Launch all device workers in parallel.
        Returns exit code:
          0 = all passed
          1 = some failed/error/unknown
          3 = no devices
        """
        if not self._devices:
            logger.error("No devices to run")
            return 3

        start_time = now_iso()
        aggregator = ReportAggregator(
            self._artifacts, self._artifacts.run_id, start_time
        )

        results = {}
        threads = []
        lock = threading.Lock()

        def run_device(device_id: str, index: int) -> None:
            worker = DeviceWorker(
                device_id=device_id,
                device_index=index,
                config=self._config,
                artifacts=self._artifacts,
            )
            report = worker.run()
            with lock:
                results[device_id] = report

        for i, device_id in enumerate(self._devices):
            t = threading.Thread(
                target=run_device,
                args=(device_id, i),
                name=f"worker-{device_id}",
                daemon=True,
            )
            threads.append(t)
            logger.info("Starting worker thread for device: %s", device_id)

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        logger.info("All device workers completed")

        # Aggregate reports
        all_upload_failures = []
        for device_id in self._devices:
            report = results.get(device_id)
            if report:
                aggregator.add_device_report(report)

        summary = aggregator.build_summary(all_upload_failures)

        if self._config.report.junit_xml:
            aggregator.build_junit_xml()

        logger.info(
            "Run complete: %s | total=%d passed=%d failed=%d error=%d unknown=%d",
            summary.status,
            summary.total, summary.passed, summary.failed,
            summary.error, summary.unknown,
        )
        logger.info("Artifacts: %s", self._artifacts.run_dir)

        if summary.status == "passed" or summary.total == 0:
            return 0
        return 1
