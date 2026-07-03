"""Configuration loader: YAML file -> Config object."""
from __future__ import annotations

import copy
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def _deep_merge(base: dict, override: dict) -> dict:
    """
    Recursively merge *override* into *base*.

    *base* is modified in place and then returned.  Nested dicts are merged
    recursively; all other value types are replaced by the override value.
    """
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


# ─── Section dataclasses ──────────────────────────────────────────────────────

@dataclass
class AppConfig:
    """Configuration for the application under test."""

    package: str = ""
    activity: str = ""
    install_path: str = ""
    reset_before_case: bool = False
    restart_before_case: bool = True
    reset_after_case: bool = False
    grant_permissions_before_case: bool = True
    clear_cache_before_case: bool = False


@dataclass
class ExecutionConfig:
    """Test execution controls."""

    tests: str = "tests"
    markers: str = ""
    keyword: str = ""
    reruns: int = 0
    device_offline_timeout_seconds: int = 60


@dataclass
class PerformanceConfig:
    """Performance sampling configuration."""

    enabled: bool = True
    metrics: list[str] = field(default_factory=lambda: ["cpu", "memory"])
    interval_seconds: float = 2.0


@dataclass
class ReportConfig:
    """Artifact and report output configuration."""

    artifacts_dir: str = "artifacts"
    upload_url: str = ""
    upload_timeout_seconds: int = 10
    junit_xml: bool = True
    save_raw_logcat: bool = True


@dataclass
class LogcatFilters:
    """Logcat filter criteria."""

    package: str = ""
    keywords: list[str] = field(default_factory=list)


@dataclass
class LogcatConfig:
    """Logcat capture configuration."""

    enabled: bool = False
    clear_before_run: bool = True
    filters: LogcatFilters = field(default_factory=LogcatFilters)


@dataclass
class SecurityConfig:
    """Security and data-sanitisation configuration."""

    sanitize_enabled: bool = True
    upload_raw_logcat: bool = False
    custom_sensitive_patterns: list[str] = field(default_factory=list)


@dataclass
class WaitConfig:
    """Implicit wait and polling configuration."""

    default_timeout_seconds: float = 10.0
    poll_interval_seconds: float = 0.5
    page_stable_timeout_seconds: float = 3.0


@dataclass
class AppiumConfig:
    """Appium server connection and startup configuration."""

    host: str = "127.0.0.1"
    base_port: int = 4723
    system_base_port: int = 8200
    mjpeg_base_port: int = 9100
    new_command_timeout: int = 120
    startup_timeout_seconds: int = 30


# ─── Top-level Config ─────────────────────────────────────────────────────────

@dataclass
class Config:
    """
    Top-level configuration object for the Mobile Automation Framework.

    All fields are typed dataclasses, giving IDE auto-complete and
    ``mypy`` coverage across the framework without any magic attribute
    look-ups.
    """

    app: AppConfig = field(default_factory=AppConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    logcat: LogcatConfig = field(default_factory=LogcatConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    wait: WaitConfig = field(default_factory=WaitConfig)
    appium: AppiumConfig = field(default_factory=AppiumConfig)

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        """
        Build a :class:`Config` from a raw dictionary (e.g. loaded from YAML).

        Unknown keys are silently ignored so that future YAML additions do
        not break older framework versions.
        """
        def _get(section: dict, key: str, default: Any) -> Any:
            return section.get(key, default)

        app_d: dict = d.get("app", {})
        exec_d: dict = d.get("execution", {})
        perf_d: dict = d.get("performance", {})
        report_d: dict = d.get("report", {})
        logcat_d: dict = d.get("logcat", {})
        sec_d: dict = d.get("security", {})
        wait_d: dict = d.get("wait", {})
        appium_d: dict = d.get("appium", {})
        logcat_filters_d: dict = logcat_d.get("filters", {})

        return cls(
            app=AppConfig(
                package=_get(app_d, "package", ""),
                activity=_get(app_d, "activity", ""),
                install_path=_get(app_d, "install_path", ""),
                reset_before_case=_get(app_d, "reset_before_case", False),
                restart_before_case=_get(app_d, "restart_before_case", True),
                reset_after_case=_get(app_d, "reset_after_case", False),
                grant_permissions_before_case=_get(app_d, "grant_permissions_before_case", True),
                clear_cache_before_case=_get(app_d, "clear_cache_before_case", False),
            ),
            execution=ExecutionConfig(
                tests=_get(exec_d, "tests", "tests"),
                markers=_get(exec_d, "markers", ""),
                keyword=_get(exec_d, "keyword", ""),
                reruns=_get(exec_d, "reruns", 0),
                device_offline_timeout_seconds=_get(
                    exec_d, "device_offline_timeout_seconds", 60
                ),
            ),
            performance=PerformanceConfig(
                enabled=_get(perf_d, "enabled", True),
                metrics=_get(perf_d, "metrics", ["cpu", "memory"]),
                interval_seconds=float(_get(perf_d, "interval_seconds", 2.0)),
            ),
            report=ReportConfig(
                artifacts_dir=_get(report_d, "artifacts_dir", "artifacts"),
                upload_url=_get(report_d, "upload_url", ""),
                upload_timeout_seconds=_get(report_d, "upload_timeout_seconds", 10),
                junit_xml=_get(report_d, "junit_xml", True),
                save_raw_logcat=_get(report_d, "save_raw_logcat", True),
            ),
            logcat=LogcatConfig(
                enabled=_get(logcat_d, "enabled", False),
                clear_before_run=_get(logcat_d, "clear_before_run", True),
                filters=LogcatFilters(
                    package=_get(logcat_filters_d, "package", ""),
                    keywords=_get(logcat_filters_d, "keywords", []),
                ),
            ),
            security=SecurityConfig(
                sanitize_enabled=_get(sec_d, "sanitize_enabled", True),
                upload_raw_logcat=_get(sec_d, "upload_raw_logcat", False),
                custom_sensitive_patterns=_get(sec_d, "custom_sensitive_patterns", []),
            ),
            wait=WaitConfig(
                default_timeout_seconds=float(
                    _get(wait_d, "default_timeout_seconds", 10.0)
                ),
                poll_interval_seconds=float(
                    _get(wait_d, "poll_interval_seconds", 0.5)
                ),
                page_stable_timeout_seconds=float(
                    _get(wait_d, "page_stable_timeout_seconds", 3.0)
                ),
            ),
            appium=AppiumConfig(
                host=_get(appium_d, "host", "127.0.0.1"),
                base_port=int(_get(appium_d, "base_port", 4723)),
                system_base_port=int(_get(appium_d, "system_base_port", 8200)),
                mjpeg_base_port=int(_get(appium_d, "mjpeg_base_port", 9100)),
                new_command_timeout=int(_get(appium_d, "new_command_timeout", 120)),
                startup_timeout_seconds=int(
                    _get(appium_d, "startup_timeout_seconds", 30)
                ),
            ),
        )


# ─── Public loader ────────────────────────────────────────────────────────────

def load_config(
    config_path: str | Path | None = None,
    overrides: dict | None = None,
) -> Config:
    """
    Load configuration from a YAML file and apply runtime overrides.

    Priority (highest → lowest):

    1. *overrides* dict (e.g. from CLI flags)
    2. YAML file at *config_path*
    3. Dataclass field defaults

    Args:
        config_path: Path to a YAML config file.  Defaults to
            ``automation_framework/config/default.yaml`` relative to this
            module's package root.
        overrides:   Optional dict with the same structure as the YAML file.
            Nested dicts are deep-merged so only the specified keys are
            overridden.

    Returns:
        A fully populated :class:`Config` instance.
    """
    if config_path is None:
        # Resolve relative to the package root: automation_framework/config/default.yaml
        config_path = (
            Path(__file__).parent.parent.parent / "config" / "default.yaml"
        )

    config_path = Path(config_path)
    raw: dict = {}

    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh)
            if isinstance(loaded, dict):
                raw = loaded
        logger.debug("Loaded config from %s", config_path)
    else:
        logger.warning(
            "Config file not found: %s — using defaults", config_path
        )

    if overrides:
        raw = _deep_merge(raw, copy.deepcopy(overrides))

    return Config.from_dict(raw)
