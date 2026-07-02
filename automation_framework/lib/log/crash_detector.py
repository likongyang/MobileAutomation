"""Crash and ANR detector - scans logcat for known crash patterns."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation_framework.lib.log.logcat import LogcatCollector

logger = logging.getLogger(__name__)


class CrashType(str, Enum):
    JAVA_CRASH = "java_crash"
    NATIVE_CRASH = "native_crash"
    ANR = "anr"


@dataclass
class CrashEvent:
    crash_type: CrashType
    message: str
    log_excerpt: str = ""
    detected_at: str = ""


CRASH_PATTERNS = [
    (CrashType.JAVA_CRASH, re.compile(r'FATAL EXCEPTION', re.IGNORECASE)),
    (CrashType.NATIVE_CRASH, re.compile(r'Fatal signal', re.IGNORECASE)),
    (CrashType.NATIVE_CRASH, re.compile(r'tombstone', re.IGNORECASE)),
    (CrashType.ANR, re.compile(r'Application Not Responding', re.IGNORECASE)),
    (CrashType.ANR, re.compile(r'ANR in', re.IGNORECASE)),
]


class CrashDetector:
    """
    Scans logcat buffer for crash and ANR events.
    """

    def __init__(self, package: str, logcat: 'LogcatCollector'):
        self.package = package
        self._logcat = logcat

    def check(self) -> list[CrashEvent]:
        """
        Scan the current logcat buffer for crash/ANR patterns.
        Returns list of detected events.
        """
        from automation_framework.lib.utils.time import now_iso
        window = self._logcat.get_window(500)
        events: list[CrashEvent] = []

        for crash_type, pattern in CRASH_PATTERNS:
            for line in window.split('\n'):
                if pattern.search(line):
                    # Extract context lines around the match
                    lines = window.split('\n')
                    idx = lines.index(line) if line in lines else -1
                    if idx >= 0:
                        start = max(0, idx - 5)
                        end = min(len(lines), idx + 20)
                        excerpt = '\n'.join(lines[start:end])
                    else:
                        excerpt = line
                    events.append(CrashEvent(
                        crash_type=crash_type,
                        message=line.strip(),
                        log_excerpt=excerpt,
                        detected_at=now_iso(),
                    ))
                    break  # one event per pattern is enough

        return events

    def check_for_package(self) -> list[CrashEvent]:
        """
        Check crashes specifically related to self.package.
        Returns events where the log mentions the package name.
        """
        all_events = self.check()
        return [
            e for e in all_events
            if self.package in e.message or self.package in e.log_excerpt
        ] or all_events  # fallback: return all if none mention package
