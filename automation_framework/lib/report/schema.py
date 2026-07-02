"""Report data schemas (dataclasses)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CaseStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


class DeviceStatus(str, Enum):
    RUNNING = "running"
    FINISHED = "finished"
    ERROR = "error"
    DISCONNECTED = "disconnected"


@dataclass
class CaseArtifacts:
    failure_screenshot: str = ""
    failure_page_source: str = ""
    failure_log: str = ""

    def to_dict(self) -> dict:
        return {
            "failure_screenshot": self.failure_screenshot,
            "failure_page_source": self.failure_page_source,
            "failure_log": self.failure_log,
        }


@dataclass
class IsolationRecord:
    restart_before_case: bool = False
    reset_before_case: bool = False
    grant_permissions_before_case: bool = False
    reset_after_case: bool = False
    clear_cache_before_case: bool = False

    def to_dict(self) -> dict:
        return {
            "restart_before_case": self.restart_before_case,
            "reset_before_case": self.reset_before_case,
            "grant_permissions_before_case": self.grant_permissions_before_case,
            "reset_after_case": self.reset_after_case,
            "clear_cache_before_case": self.clear_cache_before_case,
        }


@dataclass
class CaseReport:
    nodeid: str
    device_id: str
    status: CaseStatus = CaseStatus.UNKNOWN
    start_time: str = ""
    end_time: str = ""
    duration_seconds: float = 0.0
    failure_type: str = ""
    message: str = ""
    artifacts: CaseArtifacts = field(default_factory=CaseArtifacts)
    isolation: IsolationRecord = field(default_factory=IsolationRecord)
    crash_events: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "nodeid": self.nodeid,
            "device_id": self.device_id,
            "status": self.status.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": round(self.duration_seconds, 3),
            "failure_type": self.failure_type,
            "message": self.message,
            "artifacts": self.artifacts.to_dict(),
            "isolation": self.isolation.to_dict(),
            "crash_events": self.crash_events,
        }


@dataclass
class DeviceReport:
    device_id: str
    run_id: str
    status: DeviceStatus = DeviceStatus.RUNNING
    device_info_path: str = ""
    appium_log_path: str = ""
    cases: list[CaseReport] = field(default_factory=list)
    performance: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "run_id": self.run_id,
            "status": self.status.value,
            "device_info": self.device_info_path,
            "appium_log": self.appium_log_path,
            "cases": [c.to_dict() for c in self.cases],
            "performance": self.performance,
        }


@dataclass
class RunSummary:
    run_id: str
    status: str = "unknown"
    start_time: str = ""
    end_time: str = ""
    duration_seconds: float = 0.0
    total: int = 0
    passed: int = 0
    failed: int = 0
    error: int = 0
    skipped: int = 0
    unknown: int = 0
    devices: list[dict] = field(default_factory=list)
    environment_check: str = "environment_check.json"
    upload_failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": round(self.duration_seconds, 1),
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "error": self.error,
            "skipped": self.skipped,
            "unknown": self.unknown,
            "devices": self.devices,
            "environment_check": self.environment_check,
            "upload_failures": self.upload_failures,
        }
