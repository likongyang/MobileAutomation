"""
CLI entry point for the automation framework.

Usage::

    python -m automation_framework run [options]
    python -m automation_framework check          # env check only

Exit codes
----------
0   All checks passed / all tests passed.
1   Argument / usage error.
2   Environment check failure (non-device issue).
3   No devices found / specified devices not online.
4+  Test suite failure (forwarded from pytest / scheduler).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("automation_framework")


# ──────────────────────────────────── parser ──────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser.

    The parser exposes two sub-commands:

    * ``run``   – execute the test suite on one or more devices.
    * ``check`` – run environment pre-flight checks only (no tests).

    Returns:
        A configured :class:`argparse.ArgumentParser` instance.
    """
    parser = argparse.ArgumentParser(
        prog="python -m automation_framework",
        description="Mobile UI Automation Testing Framework v0.2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m automation_framework check\n"
            "  python -m automation_framework run --devices emulator-5554\n"
            "  python -m automation_framework run --markers smoke --reruns 2\n"
        ),
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.2.0")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── run ──────────────────────────────────────────────────────────────────
    run_parser = subparsers.add_parser(
        "run",
        help="Run the test suite on device(s)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    run_parser.add_argument(
        "--devices",
        default="",
        metavar="SERIAL[,SERIAL…]",
        help="Comma-separated device serial numbers (default: all online devices)",
    )
    run_parser.add_argument(
        "--config",
        default="",
        metavar="PATH",
        help=(
            "Path to a YAML config file "
            "(default: automation_framework/config/default.yaml)"
        ),
    )
    run_parser.add_argument(
        "--tests",
        default="",
        metavar="PATH",
        help="Test directory or file (default: taken from config)",
    )
    run_parser.add_argument(
        "--markers",
        default="",
        metavar="EXPR",
        help="pytest mark expression, e.g. ``smoke`` or ``smoke and not slow``",
    )
    run_parser.add_argument(
        "--keyword",
        default="",
        metavar="EXPR",
        help="pytest -k keyword expression",
    )
    run_parser.add_argument(
        "--reruns",
        type=int,
        default=-1,
        metavar="N",
        help="Number of times to re-run a failing test (default: taken from config)",
    )
    run_parser.add_argument(
        "--collect-performance",
        dest="collect_performance",
        choices=["true", "false"],
        default="",
        help="Enable or disable performance metric collection",
    )
    run_parser.add_argument(
        "--performance-metrics",
        dest="performance_metrics",
        default="",
        metavar="METRICS",
        help="Comma-separated metrics to collect, e.g. ``cpu,memory``",
    )
    run_parser.add_argument(
        "--artifacts-dir",
        dest="artifacts_dir",
        default="",
        metavar="DIR",
        help="Directory for test artifacts (default: artifacts/)",
    )
    run_parser.add_argument(
        "--upload-url",
        dest="upload_url",
        default="",
        metavar="URL",
        help="Remote URL to upload test results to",
    )
    run_parser.add_argument(
        "--app-package",
        dest="app_package",
        default="",
        metavar="PKG",
        help="Android application package name (e.g. com.example.app)",
    )
    run_parser.add_argument(
        "--app-activity",
        dest="app_activity",
        default="",
        metavar="ACTIVITY",
        help="Android application launch activity (e.g. .MainActivity)",
    )

    # ── check ─────────────────────────────────────────────────────────────────
    check_parser = subparsers.add_parser(
        "check",
        help="Run environment pre-flight checks only (no tests executed)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    check_parser.add_argument(
        "--config",
        default="",
        metavar="PATH",
        help="Path to a YAML config file",
    )
    check_parser.add_argument(
        "--devices",
        default="",
        metavar="SERIAL[,SERIAL…]",
        help="Comma-separated device serial numbers to verify",
    )
    check_parser.add_argument(
        "--artifacts-dir",
        dest="artifacts_dir",
        default="artifacts",
        metavar="DIR",
        help="Directory for environment check report (default: artifacts/)",
    )

    return parser


# ──────────────────────────────── helpers ─────────────────────────────────────


def _build_overrides(args: argparse.Namespace) -> dict:
    """Translate parsed CLI arguments into a nested config override dictionary.

    Only non-empty / non-sentinel values are included so that they layer cleanly
    on top of the loaded YAML config without clobbering defaults.

    Args:
        args: Parsed :class:`argparse.Namespace` from the ``run`` sub-command.

    Returns:
        A (possibly empty) nested ``dict`` ready to pass to
        :func:`automation_framework.lib.utils.config_loader.load_config`.
    """
    overrides: dict = {}

    if getattr(args, "app_package", ""):
        overrides.setdefault("app", {})["package"] = args.app_package
    if getattr(args, "app_activity", ""):
        overrides.setdefault("app", {})["activity"] = args.app_activity
    if getattr(args, "tests", ""):
        overrides.setdefault("execution", {})["tests"] = args.tests
    if getattr(args, "markers", ""):
        overrides.setdefault("execution", {})["markers"] = args.markers
    if getattr(args, "keyword", ""):
        overrides.setdefault("execution", {})["keyword"] = args.keyword
    if getattr(args, "reruns", -1) >= 0:
        overrides.setdefault("execution", {})["reruns"] = args.reruns
    if getattr(args, "collect_performance", ""):
        overrides.setdefault("performance", {})["enabled"] = (
            args.collect_performance == "true"
        )
    if getattr(args, "performance_metrics", ""):
        overrides.setdefault("performance", {})["metrics"] = [
            m.strip()
            for m in args.performance_metrics.split(",")
            if m.strip()
        ]
    if getattr(args, "artifacts_dir", ""):
        overrides.setdefault("report", {})["artifacts_dir"] = args.artifacts_dir
    if getattr(args, "upload_url", ""):
        overrides.setdefault("report", {})["upload_url"] = args.upload_url

    return overrides


def _device_check_failed(report) -> bool:  # type: ignore[no-untyped-def]
    """Return ``True`` when the device-connection check was the failing check."""
    device_check = next(
        (c for c in report.checks if c.name == "Device connection"), None
    )
    return device_check is not None and not device_check.passed


# ──────────────────────────────── sub-commands ────────────────────────────────


def cmd_check(args: argparse.Namespace) -> int:
    """Execute the ``check`` sub-command.

    Loads the framework config, runs all environment pre-flight checks, writes
    the JSON report to the artifacts directory, and exits with an appropriate
    code.

    Args:
        args: Parsed CLI arguments for the ``check`` sub-command.

    Returns:
        * ``0`` – all checks passed.
        * ``2`` – one or more non-device checks failed.
        * ``3`` – device-connectivity check failed.
    """
    from automation_framework.lib.utils.config_loader import load_config  # noqa: PLC0415
    from automation_framework.lib.environment.checker import EnvironmentChecker  # noqa: PLC0415
    from automation_framework.lib.artifacts.manager import ArtifactsManager  # noqa: PLC0415
    from automation_framework.lib.utils.file import write_json  # noqa: PLC0415

    config_path: str | None = args.config or None
    config = load_config(config_path)

    devices: list[str] | None = (
        [d.strip() for d in args.devices.split(",") if d.strip()]
        if args.devices
        else None
    )

    artifacts = ArtifactsManager(args.artifacts_dir)
    checker = EnvironmentChecker(config, devices)
    report = checker.run()

    output_path = artifacts.env_check_path()
    write_json(output_path, report.to_dict())
    logger.info("Environment check report written to: %s", output_path)

    if not report.overall_passed:
        logger.error("Environment check FAILED — aborting")
        return 3 if _device_check_failed(report) else 2

    logger.info("Environment check PASSED — all systems go")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Execute the ``run`` sub-command.

    Workflow:

    1. Load and merge configuration (file + CLI overrides).
    2. Run environment pre-flight checks.
    3. Determine the target device list.
    4. Delegate test execution to :class:`~automation_framework.lib.device.scheduler.DeviceScheduler`.

    Args:
        args: Parsed CLI arguments for the ``run`` sub-command.

    Returns:
        * ``0`` – tests passed.
        * ``2`` – environment check failure (non-device).
        * ``3`` – no devices available.
        * Any other non-zero value forwarded from the scheduler / pytest.
    """
    from automation_framework.lib.utils.config_loader import load_config  # noqa: PLC0415
    from automation_framework.lib.environment.checker import EnvironmentChecker  # noqa: PLC0415
    from automation_framework.lib.artifacts.manager import ArtifactsManager  # noqa: PLC0415
    from automation_framework.lib.device.scheduler import DeviceScheduler  # noqa: PLC0415
    from automation_framework.lib.utils.file import write_json  # noqa: PLC0415

    # ── config ────────────────────────────────────────────────────────────────
    config_path: str | None = args.config or None
    overrides = _build_overrides(args)
    config = load_config(config_path, overrides)

    # ── devices ───────────────────────────────────────────────────────────────
    devices_arg: list[str] | None = (
        [d.strip() for d in args.devices.split(",") if d.strip()]
        if args.devices
        else None
    )

    # ── artifacts ─────────────────────────────────────────────────────────────
    artifacts = ArtifactsManager(config.report.artifacts_dir)
    logger.info("Run ID   : %s", artifacts.run_id)
    logger.info("Artifacts: %s", artifacts.run_dir)

    # ── environment checks ────────────────────────────────────────────────────
    checker = EnvironmentChecker(config, devices_arg)
    env_report = checker.run()
    write_json(artifacts.env_check_path(), env_report.to_dict())

    if not env_report.overall_passed:
        logger.error("Environment check FAILED — aborting test execution")
        return 3 if _device_check_failed(env_report) else 2

    # ── determine device list ─────────────────────────────────────────────────
    devices_to_run: list[str] = (
        devices_arg if devices_arg else env_report.available_devices
    )
    if not devices_to_run:
        logger.error("No online devices available — aborting")
        return 3

    logger.info("Target device(s): %s", ", ".join(devices_to_run))

    # ── run ───────────────────────────────────────────────────────────────────
    scheduler = DeviceScheduler(config, devices_to_run, artifacts)
    return scheduler.run()


# ──────────────────────────────────── main ────────────────────────────────────


def main() -> None:
    """Parse CLI arguments and dispatch to the appropriate sub-command handler.

    This function is the sole entry point when the package is invoked as
    ``python -m automation_framework``.  It exits via :func:`sys.exit` with
    the integer exit code returned by the dispatched handler.
    """
    parser = build_parser()
    args = parser.parse_args()

    match args.command:
        case "check":
            sys.exit(cmd_check(args))
        case "run":
            sys.exit(cmd_run(args))
        case _:
            parser.print_help()
            sys.exit(1)


if __name__ == "__main__":
    main()
