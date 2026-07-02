"""Report aggregator — builds summary.json and junit.xml from all device reports."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from automation_framework.lib.report.schema import (
    CaseStatus,
    DeviceReport,
    DeviceStatus,
    RunSummary,
)
from automation_framework.lib.utils.file import write_json
from automation_framework.lib.utils.time import now_iso

if TYPE_CHECKING:
    from automation_framework.lib.artifacts.manager import ArtifactsManager

logger = logging.getLogger(__name__)


class ReportAggregator:
    """Aggregates per-device reports into a global ``summary.json`` and optional ``junit.xml``.

    Usage pattern::

        aggregator = ReportAggregator(artifacts, run_id, start_time)

        # Called once per device worker as it finishes:
        aggregator.add_device_report(device_report)

        # Called at the very end of the run:
        summary = aggregator.build_summary(upload_failures)
        aggregator.build_junit_xml()

    Args:
        artifacts: The :class:`ArtifactsManager` that resolves output paths.
        run_id: Unique identifier for this test run (e.g. a UUID or timestamp slug).
        start_time: ISO-8601 timestamp marking when the run began.
    """

    def __init__(
        self,
        artifacts: "ArtifactsManager",
        run_id: str,
        start_time: str,
    ) -> None:
        self._artifacts = artifacts
        self._run_id = run_id
        self._start_time = start_time
        self._device_reports: list[DeviceReport] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_device_report(self, report: DeviceReport) -> None:
        """Register *report* and immediately persist it as ``device_report.json``.

        Calling this method for each device as it finishes allows partial results
        to be available on disk even if the overall run is interrupted.

        Args:
            report: A completed (or final-state) :class:`~schema.DeviceReport`.
        """
        self._device_reports.append(report)
        path = self._artifacts.device_report_path(report.device_id)
        write_json(path, report.to_dict())
        logger.info("Device report saved: %s", path)

    def build_summary(self, upload_failures: list[str] | None = None) -> RunSummary:
        """Aggregate all device reports into ``summary.json`` and return the object.

        Counts are derived by iterating every :class:`~schema.CaseReport` across
        all registered device reports. The overall run status is determined as:

        * ``"failed"``  — any case is ``FAILED``, ``ERROR``, or ``UNKNOWN``
        * ``"no_tests"`` — no cases were collected at all
        * ``"passed"``  — all cases passed

        Args:
            upload_failures: Optional list of upload-failure messages to embed in
                the summary for diagnostic purposes.

        Returns:
            The populated :class:`~schema.RunSummary` instance (also written to disk).
        """
        end_time = now_iso()

        total = passed = failed = error = skipped = unknown = 0

        for dr in self._device_reports:
            for case in dr.cases:
                total += 1
                match case.status:
                    case CaseStatus.PASSED:
                        passed += 1
                    case CaseStatus.FAILED:
                        failed += 1
                    case CaseStatus.ERROR:
                        error += 1
                    case CaseStatus.SKIPPED:
                        skipped += 1
                    case CaseStatus.UNKNOWN:
                        unknown += 1

        if failed > 0 or error > 0 or unknown > 0:
            overall_status = "failed"
        elif total == 0:
            overall_status = "no_tests"
        else:
            overall_status = "passed"

        duration = self._compute_duration(self._start_time, end_time)

        devices_summary = [
            {
                "device_id": dr.device_id,
                "status": dr.status.value,
                "total": len(dr.cases),
                "passed": sum(1 for c in dr.cases if c.status == CaseStatus.PASSED),
                "failed": sum(1 for c in dr.cases if c.status == CaseStatus.FAILED),
                "error": sum(1 for c in dr.cases if c.status == CaseStatus.ERROR),
                "unknown": sum(1 for c in dr.cases if c.status == CaseStatus.UNKNOWN),
            }
            for dr in self._device_reports
        ]

        summary = RunSummary(
            run_id=self._run_id,
            status=overall_status,
            start_time=self._start_time,
            end_time=end_time,
            duration_seconds=duration,
            total=total,
            passed=passed,
            failed=failed,
            error=error,
            skipped=skipped,
            unknown=unknown,
            devices=devices_summary,
            upload_failures=upload_failures or [],
        )

        summary_path = self._artifacts.summary_path()
        write_json(summary_path, summary.to_dict())
        logger.info("Summary saved: %s", summary_path)
        return summary

    def build_junit_xml(self) -> str | None:
        """Build and write ``junit.xml`` from all registered device reports.

        Produces a standard JUnit XML document compatible with CI systems such as
        Jenkins, GitLab CI, and GitHub Actions. Each device maps to a
        ``<testsuite>``; each case maps to a ``<testcase>``.

        Returns:
            Absolute path of the written file, or ``None`` if an error occurred.
        """
        try:
            xml_path = self._artifacts.junit_xml_path()
            lines: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>']
            lines.append("<testsuites>")

            for dr in self._device_reports:
                total = len(dr.cases)
                failures = sum(1 for c in dr.cases if c.status == CaseStatus.FAILED)
                errors = sum(1 for c in dr.cases if c.status == CaseStatus.ERROR)

                lines.append(
                    f'  <testsuite name="{_escape_xml(dr.device_id)}" tests="{total}" '
                    f'failures="{failures}" errors="{errors}">'
                )

                for case in dr.cases:
                    class_name, _, test_name = case.nodeid.rpartition("::")
                    class_name = class_name.replace("/", ".").replace(".py", "")
                    duration = f"{case.duration_seconds:.3f}"

                    lines.append(
                        f'    <testcase classname="{_escape_xml(class_name)}" '
                        f'name="{_escape_xml(test_name)}" time="{duration}">'
                    )

                    match case.status:
                        case CaseStatus.FAILED:
                            msg = _escape_xml(case.message[:500])
                            lines.append(f'      <failure message="{msg}"/>')
                        case CaseStatus.ERROR:
                            msg = _escape_xml(case.message[:500])
                            lines.append(f'      <error message="{msg}"/>')
                        case CaseStatus.SKIPPED:
                            lines.append("      <skipped/>")

                    lines.append("    </testcase>")
                lines.append("  </testsuite>")

            lines.append("</testsuites>")
            xml_content = "\n".join(lines)
            xml_path.write_text(xml_content, encoding="utf-8")
            logger.info("JUnit XML saved: %s", xml_path)
            return str(xml_path)

        except Exception as exc:
            logger.error("Failed to build JUnit XML: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Read-only accessors
    # ------------------------------------------------------------------

    @property
    def device_reports(self) -> list[DeviceReport]:
        """Read-only view of all registered :class:`~schema.DeviceReport` objects."""
        return list(self._device_reports)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_duration(start_iso: str, end_iso: str) -> float:
        """Return elapsed seconds between two ISO-8601 timestamps.

        Returns ``0.0`` if either timestamp is unparseable.
        """
        try:
            start = datetime.fromisoformat(start_iso)
            end = datetime.fromisoformat(end_iso)
            return (end - start).total_seconds()
        except Exception:
            return 0.0


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _escape_xml(text: str) -> str:
    """Escape the five predefined XML entities in *text*.

    Args:
        text: Raw string that may contain ``&``, ``<``, ``>``, ``"``, or ``'``.

    Returns:
        The escaped string safe for inclusion in XML attributes and text nodes.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
