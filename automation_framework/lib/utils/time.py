"""Time and run ID utilities."""
from __future__ import annotations

import datetime
import random
import string
import time


def now_iso() -> str:
    """Return current time as ISO 8601 string with timezone."""
    return datetime.datetime.now().astimezone().isoformat()


def utcnow_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def generate_run_id() -> str:
    """Generate a unique run ID: YYYYMMDD_HHMMSS_xxxx."""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    rand = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{ts}_{rand}"


def monotonic() -> float:
    """Return monotonic clock value in seconds."""
    return time.monotonic()


def current_timestamp() -> float:
    """Return current Unix timestamp."""
    return time.time()
