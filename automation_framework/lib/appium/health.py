"""Appium server health check."""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)


def wait_for_appium(
    host: str,
    port: int,
    timeout: float = 30.0,
    interval: float = 1.0,
) -> bool:
    """
    Poll /status endpoint until Appium responds or timeout expires.
    Returns True if healthy, False on timeout.
    """
    url = f"http://{host}:{port}/status"
    deadline = time.monotonic() + timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            resp = requests.get(url, timeout=2)
            if resp.status_code == 200:
                logger.info(
                    "Appium:%d is healthy (attempt %d, %.1fs elapsed)",
                    port, attempt, timeout - (deadline - time.monotonic())
                )
                return True
        except requests.exceptions.ConnectionError:
            pass
        except Exception as e:
            logger.debug("Appium health check error: %s", e)
        time.sleep(interval)

    logger.error("Appium:%d did not become healthy within %.0fs", port, timeout)
    return False
