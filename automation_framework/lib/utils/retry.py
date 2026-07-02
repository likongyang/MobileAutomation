"""Retry utilities."""
from __future__ import annotations

import functools
import logging
import time
from typing import Callable, TypeVar, Any, Type

logger = logging.getLogger(__name__)

F = TypeVar('F', bound=Callable[..., Any])


def retry(
    times: int = 3,
    delay: float = 1.0,
    backoff: float = 1.0,
    exceptions: tuple[Type[Exception], ...] = (Exception,),
    logger_name: str | None = None,
) -> Callable[[F], F]:
    """
    Retry decorator with configurable attempts, delay and backoff.

    Args:
        times:       Maximum number of attempts (including the first).
        delay:       Initial wait between attempts in seconds.
        backoff:     Multiplier applied to delay after each failure (1.0 = no backoff).
        exceptions:  Exception types that trigger a retry.
        logger_name: Logger name to use; defaults to the decorated function's module.

    Example::

        @retry(times=3, delay=0.5, backoff=2.0, exceptions=(IOError,))
        def flaky_call():
            ...
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            log = logging.getLogger(logger_name or func.__module__)
            last_exc: Exception | None = None
            current_delay = delay
            for attempt in range(1, times + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < times:
                        log.warning(
                            "[retry] %s failed (attempt %d/%d): %s — retrying in %.1fs",
                            func.__name__, attempt, times, e, current_delay,
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
            raise last_exc  # type: ignore[misc]
        return wrapper  # type: ignore[return-value]
    return decorator


def wait_until(
    condition: Callable[[], bool],
    timeout: float = 10.0,
    interval: float = 0.5,
    desc: str = "condition",
) -> bool:
    """
    Poll ``condition()`` until it returns ``True`` or ``timeout`` elapses.

    Args:
        condition: Zero-argument callable that returns a boolean.
        timeout:   Maximum seconds to wait.
        interval:  Seconds between polls.
        desc:      Human-readable description used in log messages.

    Returns:
        ``True`` if the condition was met within the timeout, ``False`` otherwise.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    logger.debug("wait_until: '%s' not satisfied after %.1fs", desc, timeout)
    return False
