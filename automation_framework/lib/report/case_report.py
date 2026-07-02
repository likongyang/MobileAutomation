"""Case report writer and remote uploader."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from automation_framework.lib.report.schema import CaseReport
from automation_framework.lib.utils.file import write_json

if TYPE_CHECKING:
    from automation_framework.lib.artifacts.manager import ArtifactsManager
    from automation_framework.lib.log.sanitizer import Sanitizer

logger = logging.getLogger(__name__)


class CaseReporter:
    """
    Writes case results to case.json and optionally uploads to a remote URL.

    Each test case execution produces a structured JSON artifact (``case.json``)
    stored under the device's artifact directory. Optionally, the report can be
    POSTed to a remote endpoint for real-time ingestion (e.g. a CI dashboard).

    Upload failures are recorded internally and never propagate to the test run —
    a failed upload does not mark a test as failed.

    Args:
        artifacts: The :class:`ArtifactsManager` that resolves output paths.
        sanitizer: Optional sanitizer applied to report dicts before write/upload.
        upload_url: HTTP(S) endpoint to POST case JSON to. Empty string disables upload.
        upload_timeout: Timeout in seconds for the upload HTTP request.
    """

    def __init__(
        self,
        artifacts: "ArtifactsManager",
        sanitizer: "Sanitizer | None" = None,
        upload_url: str = "",
        upload_timeout: int = 10,
    ) -> None:
        self._artifacts = artifacts
        self._sanitizer = sanitizer
        self._upload_url = upload_url
        self._upload_timeout = upload_timeout
        self._upload_failures: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, report: CaseReport) -> str:
        """Serialise and write *report* to ``case.json`` on disk.

        The report dict is optionally sanitized before writing. The destination
        path is resolved via :meth:`ArtifactsManager.case_json_path`.

        Args:
            report: The fully populated :class:`~schema.CaseReport` to persist.

        Returns:
            Absolute path of the written file as a string.
        """
        data = report.to_dict()
        if self._sanitizer:
            data = self._sanitizer.sanitize_dict(data)

        path = self._artifacts.case_json_path(report.device_id, report.nodeid)
        write_json(path, data)
        logger.debug("Case report saved: %s", path)
        return str(path)

    def upload(self, report: CaseReport) -> bool:
        """POST *report* to the configured remote URL.

        The method is a no-op (returns ``True``) when ``upload_url`` is empty.
        Any network or HTTP error is caught, logged, and recorded in
        :attr:`upload_failures`; it does **not** raise.

        Args:
            report: The :class:`~schema.CaseReport` to upload.

        Returns:
            ``True`` if the upload succeeded (or was skipped), ``False`` on error.
        """
        if not self._upload_url:
            return True

        try:
            import requests  # optional dependency — only needed when uploading

            data = report.to_dict()
            if self._sanitizer:
                data = self._sanitizer.sanitize_dict(data)

            resp = requests.post(
                self._upload_url,
                json=data,
                timeout=self._upload_timeout,
            )
            if resp.status_code in (200, 201, 202, 204):
                logger.info("Case report uploaded: %s", report.nodeid)
                return True

            msg = f"Upload failed for {report.nodeid}: HTTP {resp.status_code}"
            logger.warning(msg)
            self._upload_failures.append(msg)
            return False

        except Exception as exc:
            msg = f"Upload error for {report.nodeid}: {exc}"
            logger.warning(msg)
            self._upload_failures.append(msg)
            return False

    def save_and_upload(self, report: CaseReport) -> tuple[str, bool]:
        """Convenience method: save then upload.

        Args:
            report: The :class:`~schema.CaseReport` to persist and upload.

        Returns:
            A 2-tuple of ``(saved_path, upload_ok)``.
        """
        path = self.save(report)
        ok = self.upload(report)
        return path, ok

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def upload_failures(self) -> list[str]:
        """Accumulated upload failure messages (copies to prevent mutation)."""
        return list(self._upload_failures)
