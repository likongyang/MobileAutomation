"""
Appium client - wraps all Selenium/Appium WebDriver operations.
Business code should use TestContext, which delegates to this class.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.remote.webelement import WebElement

logger = logging.getLogger(__name__)


class ElementNotFoundError(Exception):
    """Raised when an element cannot be found within the wait timeout."""


class MultipleLocatorsError(Exception):
    """Raised when more than one locator strategy is specified."""


def _build_locator(
    id: str | None = None,
    text: str | None = None,
    contains_text: str | None = None,
    xpath: str | None = None,
    accessibility_id: str | None = None,
    class_name: str | None = None,
    uiautomator: str | None = None,
) -> tuple[str, str]:
    """
    Build a (by, value) locator tuple from keyword arguments.

    Exactly one locator strategy must be provided. The function maps each
    strategy to its corresponding ``AppiumBy`` constant and selector value.

    Args:
        id: Element resource-id (``AppiumBy.ID``).
        text: Exact text match via XPath ``@text`` attribute.
        contains_text: Partial text match via XPath ``contains(@text, ...)``.
        xpath: Raw XPath expression (``AppiumBy.XPATH``).
        accessibility_id: Accessibility ID / content-desc (``AppiumBy.ACCESSIBILITY_ID``).
        class_name: UI class name (``AppiumBy.CLASS_NAME``).
        uiautomator: Android UiAutomator selector string (``AppiumBy.ANDROID_UIAUTOMATOR``).

    Returns:
        A ``(by, value)`` tuple ready to pass to ``driver.find_element``.

    Raises:
        MultipleLocatorsError: If more than one strategy is specified.
        ValueError: If no strategy is specified.
    """
    strategies = {
        "id": (AppiumBy.ID, id),
        "text": (AppiumBy.XPATH, f'//android.widget.TextView[@text="{text}"]' if text else None),
        "contains_text": (
            AppiumBy.XPATH,
            f'//android.widget.TextView[contains(@text, "{contains_text}")]' if contains_text else None,
        ),
        "xpath": (AppiumBy.XPATH, xpath),
        "accessibility_id": (AppiumBy.ACCESSIBILITY_ID, accessibility_id),
        "class_name": (AppiumBy.CLASS_NAME, class_name),
        "uiautomator": (AppiumBy.ANDROID_UIAUTOMATOR, uiautomator),
    }
    given = {k: v for k, (_, v) in strategies.items() if v is not None}
    if len(given) > 1:
        raise MultipleLocatorsError(
            f"Multiple locators specified: {list(given.keys())}. Use only one."
        )
    if not given:
        raise ValueError(
            "No locator specified. Use one of: id, text, contains_text, xpath, "
            "accessibility_id, class_name, uiautomator."
        )
    key = next(iter(given))
    by, value = strategies[key]
    return by, value


class AppiumClient:
    """
    Wraps an Appium WebDriver session for a single device.

    Provides a high-level API for element interaction, gestures, app lifecycle
    management, and system-level actions. All element-finding methods include
    configurable elastic (polling) waits so callers do not need to add
    explicit sleeps.

    Typical usage::

        client = AppiumClient(
            appium_url="http://127.0.0.1:4723",
            device_id="emulator-5554",
            app_package="com.example.app",
            app_activity=".MainActivity",
            system_port=8200,
        )
        client.create_session()
        try:
            client.click(accessibility_id="Login")
            client.input_text(id="com.example.app:id/username", value="admin")
        finally:
            client.close_session()

    Args:
        appium_url: Full URL of the Appium server (e.g. ``http://127.0.0.1:4723``).
        device_id: Android device serial / UDID.
        app_package: Android application package name.
        app_activity: Android application launch activity.
        system_port: UiAutomator2 system port (must be unique per device).
        new_command_timeout: Seconds before Appium kills an idle session (default 120).
        default_timeout: Default element-wait timeout in seconds (default 10.0).
        poll_interval: Polling interval used during element waits (default 0.5s).
    """

    def __init__(
        self,
        appium_url: str,
        device_id: str,
        app_package: str,
        app_activity: str,
        system_port: int,
        new_command_timeout: int = 120,
        default_timeout: float = 10.0,
        poll_interval: float = 0.5,
    ):
        self.appium_url = appium_url
        self.device_id = device_id
        self.app_package = app_package
        self.app_activity = app_activity
        self.system_port = system_port
        self.new_command_timeout = new_command_timeout
        self.default_timeout = default_timeout
        self.poll_interval = poll_interval
        self.driver: webdriver.Remote | None = None

    # ─── Session management ────────────────────────────────────────────────────

    def create_session(self) -> None:
        """
        Create an Appium WebDriver session for the configured device and app.

        Builds ``AppiumOptions`` with UiAutomator2 capabilities, connects to
        the Appium server, and stores the resulting driver on ``self.driver``.

        Raises:
            WebDriverException: If the session cannot be created (e.g. device
                not found, app not installed).
        """
        options = UiAutomator2Options()
        options.platform_name = "Android"
        options.automation_name = "UiAutomator2"
        options.device_name = self.device_id
        options.udid = self.device_id
        options.app_package = self.app_package
        options.app_activity = self.app_activity
        options.new_command_timeout = self.new_command_timeout
        options.no_reset = True
        options.full_reset = False
        options.set_capability("systemPort", self.system_port)
        options.set_capability("autoGrantPermissions", True)
        options.set_capability("skipUnlock", True)
        options.set_capability("uiautomator2ServerInstallTimeout", 90000)
        options.set_capability("uiautomator2ServerLaunchTimeout", 90000)


        logger.info("Creating Appium session for device %s", self.device_id)
        self.driver = webdriver.Remote(self.appium_url, options=options)
        logger.info("Appium session created: %s", self.driver.session_id)

    def close_session(self) -> None:
        """
        Quit the active Appium WebDriver session and clear ``self.driver``.

        Silently handles exceptions so cleanup in ``finally`` blocks is safe.
        """
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Appium session closed for %s", self.device_id)
            except Exception as e:
                logger.warning("Error closing Appium session: %s", e)
            finally:
                self.driver = None

    def _require_driver(self) -> webdriver.Remote:
        """
        Return the active driver, or raise ``RuntimeError`` if no session exists.

        Returns:
            The active ``webdriver.Remote`` instance.

        Raises:
            RuntimeError: If ``create_session()`` has not been called.
        """
        if self.driver is None:
            raise RuntimeError("No active Appium session. Call create_session() first.")
        return self.driver

    # ─── Element finding (with elastic wait) ───────────────────────────────────

    def find(
        self,
        *,
        id: str | None = None,
        text: str | None = None,
        contains_text: str | None = None,
        xpath: str | None = None,
        accessibility_id: str | None = None,
        class_name: str | None = None,
        uiautomator: str | None = None,
        timeout: float | None = None,
    ) -> WebElement:
        """
        Find a single element with elastic (polling) wait.

        Exactly one locator keyword argument must be provided. The method polls
        at ``poll_interval`` until the element is found or ``timeout`` expires.

        Args:
            id: Element resource-id.
            text: Exact text of a ``TextView``.
            contains_text: Partial text of a ``TextView``.
            xpath: Raw XPath expression.
            accessibility_id: Element accessibility ID / content-desc.
            class_name: UI class name.
            uiautomator: Android UiAutomator selector string.
            timeout: Override for the default wait timeout (seconds).

        Returns:
            The first matching ``WebElement``.

        Raises:
            ElementNotFoundError: If no element is found within ``timeout``.
            MultipleLocatorsError: If more than one locator keyword is given.
            ValueError: If no locator keyword is given.
        """
        by, value = _build_locator(
            id=id, text=text, contains_text=contains_text,
            xpath=xpath, accessibility_id=accessibility_id,
            class_name=class_name, uiautomator=uiautomator
        )
        return self._wait_for_element(by, value, timeout=timeout)

    def find_all(
        self,
        *,
        id: str | None = None,
        text: str | None = None,
        contains_text: str | None = None,
        xpath: str | None = None,
        accessibility_id: str | None = None,
        class_name: str | None = None,
        uiautomator: str | None = None,
        timeout: float | None = None,
    ) -> list[WebElement]:
        """
        Find all matching elements, waiting up to ``timeout`` for at least one.

        Args:
            id: Element resource-id.
            text: Exact text of a ``TextView``.
            contains_text: Partial text of a ``TextView``.
            xpath: Raw XPath expression.
            accessibility_id: Element accessibility ID / content-desc.
            class_name: UI class name.
            uiautomator: Android UiAutomator selector string.
            timeout: Override for the default wait timeout (seconds).

        Returns:
            A list of matching ``WebElement`` objects (may be empty if timeout
            expires before any element appears).
        """
        by, value = _build_locator(
            id=id, text=text, contains_text=contains_text,
            xpath=xpath, accessibility_id=accessibility_id,
            class_name=class_name, uiautomator=uiautomator
        )
        driver = self._require_driver()
        deadline = time.monotonic() + (timeout or self.default_timeout)
        while time.monotonic() < deadline:
            elements = driver.find_elements(by, value)
            if elements:
                return elements
            time.sleep(self.poll_interval)
        return driver.find_elements(by, value)  # return empty list if still none

    def is_present(
        self,
        *,
        id: str | None = None,
        text: str | None = None,
        contains_text: str | None = None,
        xpath: str | None = None,
        accessibility_id: str | None = None,
        class_name: str | None = None,
        uiautomator: str | None = None,
        timeout: float = 2.0,
    ) -> bool:
        """
        Return ``True`` if a matching element is present within ``timeout``.

        Uses a short default timeout (2 s) so it can be used in conditionals
        without significantly slowing down tests.

        Args:
            id: Element resource-id.
            text: Exact text of a ``TextView``.
            contains_text: Partial text of a ``TextView``.
            xpath: Raw XPath expression.
            accessibility_id: Element accessibility ID / content-desc.
            class_name: UI class name.
            uiautomator: Android UiAutomator selector string.
            timeout: Maximum seconds to wait before returning ``False``.

        Returns:
            ``True`` if found, ``False`` otherwise.
        """
        try:
            self.find(
                id=id, text=text, contains_text=contains_text, xpath=xpath,
                accessibility_id=accessibility_id, class_name=class_name,
                uiautomator=uiautomator, timeout=timeout
            )
            return True
        except ElementNotFoundError:
            return False

    def wait_for_gone(
        self,
        *,
        id: str | None = None,
        text: str | None = None,
        contains_text: str | None = None,
        xpath: str | None = None,
        accessibility_id: str | None = None,
        class_name: str | None = None,
        uiautomator: str | None = None,
        timeout: float | None = None,
    ) -> bool:
        """
        Wait until a matching element disappears from the UI hierarchy.

        Useful for confirming loading indicators, dialogs, or toasts have closed.

        Args:
            id: Element resource-id.
            text: Exact text of a ``TextView``.
            contains_text: Partial text of a ``TextView``.
            xpath: Raw XPath expression.
            accessibility_id: Element accessibility ID / content-desc.
            class_name: UI class name.
            uiautomator: Android UiAutomator selector string.
            timeout: Maximum seconds to wait (default: ``self.default_timeout``).

        Returns:
            ``True`` if the element is gone before the timeout, ``False`` otherwise.
        """
        by, value = _build_locator(
            id=id, text=text, contains_text=contains_text,
            xpath=xpath, accessibility_id=accessibility_id,
            class_name=class_name, uiautomator=uiautomator
        )
        driver = self._require_driver()
        deadline = time.monotonic() + (timeout or self.default_timeout)
        while time.monotonic() < deadline:
            elements = driver.find_elements(by, value)
            if not elements:
                return True
            time.sleep(self.poll_interval)
        return False

    def _wait_for_element(
        self, by: str, value: str, timeout: float | None = None
    ) -> WebElement:
        """
        Internal polling loop: returns the first element found or raises.

        Args:
            by: Locator strategy constant from ``AppiumBy``.
            value: Selector value corresponding to the strategy.
            timeout: Seconds to poll before raising (default: ``self.default_timeout``).

        Returns:
            The first matching ``WebElement``.

        Raises:
            ElementNotFoundError: If no element is found within ``timeout``.
        """
        driver = self._require_driver()
        t = timeout if timeout is not None else self.default_timeout
        deadline = time.monotonic() + t
        while time.monotonic() < deadline:
            try:
                elements = driver.find_elements(by, value)
                if elements:
                    return elements[0]
            except (StaleElementReferenceException, WebDriverException):
                pass
            time.sleep(self.poll_interval)
        raise ElementNotFoundError(
            f"Element not found within {t}s — by={by!r}, value={value!r}"
        )

    # ─── Gestures ───────────────────────────────────────────────────────────────

    def click(
        self,
        *,
        id: str | None = None,
        text: str | None = None,
        contains_text: str | None = None,
        xpath: str | None = None,
        accessibility_id: str | None = None,
        class_name: str | None = None,
        uiautomator: str | None = None,
        timeout: float | None = None,
    ) -> None:
        """
        Find an element and tap it.

        Accepts the same locator keywords as :meth:`find`.

        Args:
            id: Element resource-id.
            text: Exact text of a ``TextView``.
            contains_text: Partial text of a ``TextView``.
            xpath: Raw XPath expression.
            accessibility_id: Element accessibility ID / content-desc.
            class_name: UI class name.
            uiautomator: Android UiAutomator selector string.
            timeout: Override for the default wait timeout (seconds).

        Raises:
            ElementNotFoundError: If the element is not found within timeout.
        """
        el = self.find(
            id=id, text=text, contains_text=contains_text, xpath=xpath,
            accessibility_id=accessibility_id, class_name=class_name,
            uiautomator=uiautomator, timeout=timeout
        )
        el.click()

    def long_press(
        self,
        *,
        id: str | None = None,
        text: str | None = None,
        xpath: str | None = None,
        accessibility_id: str | None = None,
        timeout: float | None = None,
        duration_ms: int = 1000,
    ) -> None:
        """
        Perform a long-press gesture on an element.

        Uses the ``mobile: longClickGesture`` Appium mobile command.

        Args:
            id: Element resource-id.
            text: Exact text of a ``TextView``.
            xpath: Raw XPath expression.
            accessibility_id: Element accessibility ID.
            timeout: Override for the default wait timeout (seconds).
            duration_ms: Duration of the press in milliseconds (default 1000).

        Raises:
            ElementNotFoundError: If the element is not found within timeout.
        """
        el = self.find(
            id=id, text=text, xpath=xpath,
            accessibility_id=accessibility_id, timeout=timeout
        )
        driver = self._require_driver()
        driver.execute_script(
            "mobile: longClickGesture",
            {"elementId": el.id, "duration": duration_ms}
        )

    def double_click(
        self,
        *,
        id: str | None = None,
        text: str | None = None,
        xpath: str | None = None,
        accessibility_id: str | None = None,
        timeout: float | None = None,
    ) -> None:
        """
        Perform a double-tap gesture on an element.

        Uses the ``mobile: doubleClickGesture`` Appium mobile command.

        Args:
            id: Element resource-id.
            text: Exact text of a ``TextView``.
            xpath: Raw XPath expression.
            accessibility_id: Element accessibility ID.
            timeout: Override for the default wait timeout (seconds).

        Raises:
            ElementNotFoundError: If the element is not found within timeout.
        """
        el = self.find(
            id=id, text=text, xpath=xpath,
            accessibility_id=accessibility_id, timeout=timeout
        )
        driver = self._require_driver()
        driver.execute_script(
            "mobile: doubleClickGesture",
            {"elementId": el.id}
        )

    def tap(self, x: int, y: int) -> None:
        """
        Tap at absolute screen coordinates.

        Args:
            x: Horizontal coordinate in pixels.
            y: Vertical coordinate in pixels.
        """
        driver = self._require_driver()
        driver.execute_script("mobile: clickGesture", {"x": x, "y": y})

    # ─── Text input ─────────────────────────────────────────────────────────────

    def input_text(
        self,
        *,
        value: str,
        id: str | None = None,
        text: str | None = None,
        contains_text: str | None = None,
        xpath: str | None = None,
        accessibility_id: str | None = None,
        class_name: str | None = None,
        uiautomator: str | None = None,
        timeout: float | None = None,
        clear_first: bool = True,
    ) -> None:
        """
        Find an input field element and type text into it.

        Taps the element to focus it, optionally clears existing content, then
        sends the ``value`` string via ``send_keys``.

        Args:
            value: The text string to type.
            id: Element resource-id.
            text: Exact text of a ``TextView``.
            contains_text: Partial text of a ``TextView``.
            xpath: Raw XPath expression.
            accessibility_id: Element accessibility ID.
            class_name: UI class name.
            uiautomator: Android UiAutomator selector string.
            timeout: Override for the default wait timeout (seconds).
            clear_first: If ``True`` (default), clears the field before typing.

        Raises:
            ElementNotFoundError: If the element is not found within timeout.
        """
        el = self.find(
            id=id, text=text, contains_text=contains_text, xpath=xpath,
            accessibility_id=accessibility_id, class_name=class_name,
            uiautomator=uiautomator, timeout=timeout
        )
        el.click()
        if clear_first:
            el.clear()
        el.send_keys(value)

    def clear_text(
        self,
        *,
        id: str | None = None,
        text: str | None = None,
        xpath: str | None = None,
        accessibility_id: str | None = None,
        timeout: float | None = None,
    ) -> None:
        """
        Clear the text content of an input field element.

        Args:
            id: Element resource-id.
            text: Exact text of a ``TextView``.
            xpath: Raw XPath expression.
            accessibility_id: Element accessibility ID.
            timeout: Override for the default wait timeout (seconds).

        Raises:
            ElementNotFoundError: If the element is not found within timeout.
        """
        el = self.find(
            id=id, text=text, xpath=xpath,
            accessibility_id=accessibility_id, timeout=timeout
        )
        el.clear()

    # ─── Element properties ─────────────────────────────────────────────────────

    def get_text(
        self,
        *,
        id: str | None = None,
        text: str | None = None,
        xpath: str | None = None,
        accessibility_id: str | None = None,
        timeout: float | None = None,
    ) -> str:
        """
        Return the visible text content of an element.

        Args:
            id: Element resource-id.
            text: Exact text of a ``TextView``.
            xpath: Raw XPath expression.
            accessibility_id: Element accessibility ID.
            timeout: Override for the default wait timeout (seconds).

        Returns:
            The element's ``text`` property.

        Raises:
            ElementNotFoundError: If the element is not found within timeout.
        """
        el = self.find(
            id=id, text=text, xpath=xpath,
            accessibility_id=accessibility_id, timeout=timeout
        )
        return el.text

    def get_attribute(
        self,
        attr: str,
        *,
        id: str | None = None,
        xpath: str | None = None,
        accessibility_id: str | None = None,
        timeout: float | None = None,
    ) -> str | None:
        """
        Return the value of a specific attribute from an element.

        Args:
            attr: Name of the attribute to retrieve (e.g. ``"enabled"``,
                  ``"content-desc"``, ``"resource-id"``).
            id: Element resource-id.
            xpath: Raw XPath expression.
            accessibility_id: Element accessibility ID.
            timeout: Override for the default wait timeout (seconds).

        Returns:
            The attribute value as a string, or ``None`` if absent.

        Raises:
            ElementNotFoundError: If the element is not found within timeout.
        """
        el = self.find(id=id, xpath=xpath, accessibility_id=accessibility_id, timeout=timeout)
        return el.get_attribute(attr)

    # ─── Screenshot & page source ───────────────────────────────────────────────

    def take_screenshot(self, path: str | Path) -> str:
        """
        Capture the device screen and save it to ``path``.

        Parent directories are created automatically if they do not exist.

        Args:
            path: Destination file path (e.g. ``reports/screenshots/step1.png``).

        Returns:
            The absolute path of the saved screenshot as a string.
        """
        driver = self._require_driver()
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        driver.save_screenshot(str(p))
        return str(p)

    def get_page_source(self) -> str:
        """
        Return the current UI hierarchy as XML (page source).

        Returns:
            XML string representing the current UI state.
        """
        driver = self._require_driver()
        return driver.page_source

    def save_page_source(self, path: str | Path) -> str:
        """
        Dump the current page source XML to a file.

        Parent directories are created automatically.

        Args:
            path: Destination file path.

        Returns:
            The path of the saved file as a string.
        """
        source = self.get_page_source()
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(source, encoding='utf-8')
        return str(p)

    # ─── Scrolling and swipes ───────────────────────────────────────────────────

    def swipe(
        self,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        duration_ms: int = 500,
    ) -> None:
        """
        Perform a swipe gesture between two absolute screen coordinates.

        Uses the ``mobile: swipeGesture`` Appium mobile command.

        Args:
            start_x: Starting X coordinate in pixels.
            start_y: Starting Y coordinate in pixels.
            end_x: Ending X coordinate in pixels.
            end_y: Ending Y coordinate in pixels.
            duration_ms: Swipe speed / duration in milliseconds (default 500).
        """
        driver = self._require_driver()
        driver.execute_script(
            "mobile: swipeGesture",
            {
                "startX": start_x,
                "startY": start_y,
                "endX": end_x,
                "endY": end_y,
                "speed": duration_ms,
            },
        )

    def swipe_up(self, ratio: float = 0.5) -> None:
        """
        Swipe upward (scroll down in content) from the centre of the screen.

        Args:
            ratio: Fraction of screen height to swipe (default 0.5 = 50%).
        """
        driver = self._require_driver()
        size = driver.get_window_size()
        w, h = size['width'], size['height']
        self.swipe(w * 0.5, h * 0.7, w * 0.5, h * (0.7 - ratio))

    def swipe_down(self, ratio: float = 0.5) -> None:
        """
        Swipe downward (scroll up in content) from the centre of the screen.

        Args:
            ratio: Fraction of screen height to swipe (default 0.5 = 50%).
        """
        driver = self._require_driver()
        size = driver.get_window_size()
        w, h = size['width'], size['height']
        self.swipe(w * 0.5, h * 0.3, w * 0.5, h * (0.3 + ratio))

    def swipe_left(self, ratio: float = 0.5) -> None:
        """
        Swipe left (scroll right in content) from the right side of the screen.

        Args:
            ratio: Fraction of screen width to swipe (default 0.5 = 50%).
        """
        driver = self._require_driver()
        size = driver.get_window_size()
        w, h = size['width'], size['height']
        self.swipe(w * 0.8, h * 0.5, w * (0.8 - ratio), h * 0.5)

    def swipe_right(self, ratio: float = 0.5) -> None:
        """
        Swipe right (scroll left in content) from the left side of the screen.

        Args:
            ratio: Fraction of screen width to swipe (default 0.5 = 50%).
        """
        driver = self._require_driver()
        size = driver.get_window_size()
        w, h = size['width'], size['height']
        self.swipe(w * 0.2, h * 0.5, w * (0.2 + ratio), h * 0.5)

    def scroll_find(
        self,
        *,
        id: str | None = None,
        text: str | None = None,
        contains_text: str | None = None,
        xpath: str | None = None,
        accessibility_id: str | None = None,
        direction: str = "vertical",
        max_swipes: int = 5,
        timeout: float = 10.0,
    ) -> WebElement:
        """
        Scroll to locate an element, swiping repeatedly if it is not visible.

        After each failed find attempt, one swipe is performed in ``direction``
        and the search is retried, up to ``max_swipes`` times.

        Args:
            id: Element resource-id.
            text: Exact text of a ``TextView``.
            contains_text: Partial text of a ``TextView``.
            xpath: Raw XPath expression.
            accessibility_id: Element accessibility ID.
            direction: ``"vertical"`` (swipe-up) or ``"horizontal"`` (swipe-left).
            max_swipes: Maximum number of swipes before giving up (default 5).
            timeout: Total seconds to search per swipe attempt (divided evenly).

        Returns:
            The located ``WebElement``.

        Raises:
            ElementNotFoundError: If element is not found after all swipes.
        """
        per_attempt_timeout = timeout / max_swipes if max_swipes > 0 else timeout
        for i in range(max_swipes + 1):
            try:
                return self.find(
                    id=id, text=text, contains_text=contains_text,
                    xpath=xpath, accessibility_id=accessibility_id,
                    timeout=per_attempt_timeout
                )
            except ElementNotFoundError:
                if i < max_swipes:
                    if direction == "vertical":
                        self.swipe_up()
                    else:
                        self.swipe_left()
        raise ElementNotFoundError(
            f"Element not found after {max_swipes} swipes — text={text!r}, id={id!r}"
        )

    # ─── System actions ──────────────────────────────────────────────────────────

    def back(self) -> None:
        """Press the Android hardware back button."""
        self._require_driver().back()

    def home(self) -> None:
        """Press the Android home button (keycode 3)."""
        driver = self._require_driver()
        driver.execute_script("mobile: pressKey", {"keycode": 3})

    def open_recent_apps(self) -> None:
        """Press the Android Recents / Overview button (keycode 187)."""
        driver = self._require_driver()
        driver.execute_script("mobile: pressKey", {"keycode": 187})

    def open_notifications(self) -> None:
        """Open the Android notification shade."""
        self._require_driver().open_notifications()

    def hide_keyboard(self) -> None:
        """
        Hide the software keyboard if it is currently visible.

        Silently ignores errors so it is safe to call unconditionally.
        """
        try:
            driver = self._require_driver()
            if driver.is_keyboard_shown():
                driver.hide_keyboard()
        except Exception as e:
            logger.debug("hide_keyboard: %s", e)

    # ─── App lifecycle ───────────────────────────────────────────────────────────

    def start_app(self) -> None:
        """
        Bring the configured app to the foreground (activate it).

        Uses ``driver.activate_app`` with ``self.app_package``.
        """
        driver = self._require_driver()
        driver.activate_app(self.app_package)

    def stop_app(self) -> None:
        """
        Terminate the configured app process.

        Uses ``driver.terminate_app`` with ``self.app_package``.
        """
        driver = self._require_driver()
        driver.terminate_app(self.app_package)

    def restart_app(self) -> None:
        """
        Terminate and then re-launch the configured app.

        Inserts a short pause between stop and start to allow Android to
        fully clean up the process.
        """
        self.stop_app()
        time.sleep(0.5)
        self.start_app()

    def clear_app_data(self) -> None:
        """
        Clear app data via ADB (not via Appium WebDriver).

        Raises:
            NotImplementedError: Always — use ``context.app.clear_data()`` which
                delegates to ``ADBClient`` instead.
        """
        raise NotImplementedError(
            "Use context.app.clear_data() which delegates to ADBClient"
        )

    def app_background(self, seconds: float = 5.0) -> None:
        """
        Send the app to the background for ``seconds`` seconds.

        Args:
            seconds: Number of seconds to keep the app backgrounded (default 5).
        """
        driver = self._require_driver()
        driver.background_app(int(seconds))

    def install_app(self, apk_path: str) -> None:
        """
        Install an APK onto the device.

        Args:
            apk_path: Local filesystem path to the ``.apk`` file.
        """
        driver = self._require_driver()
        driver.install_app(apk_path)

    def is_app_installed(self) -> bool:
        """
        Check whether the configured app package is installed on the device.

        Returns:
            ``True`` if installed, ``False`` otherwise.
        """
        driver = self._require_driver()
        return driver.is_app_installed(self.app_package)

    # ─── Permission dialogs ──────────────────────────────────────────────────────

    def allow_permission_if_present(self) -> bool:
        """
        Dismiss a system permission dialog by tapping an "Allow" variant button.

        Tries a set of common "allow" button texts across English and Chinese
        locales, as well as common Android permission dialog button labels.

        Returns:
            ``True`` if a dialog was successfully dismissed, ``False`` if none
            of the known button texts were found.
        """
        allow_texts = ["Allow", "允许", "ALLOW", "While using the app", "Only this time"]
        for t in allow_texts:
            try:
                self.click(text=t, timeout=1.5)
                logger.info("Permission dialog dismissed: '%s'", t)
                return True
            except ElementNotFoundError:
                continue
        return False

    def deny_permission_if_present(self) -> bool:
        """
        Dismiss a system permission dialog by tapping a "Deny" variant button.

        Returns:
            ``True`` if a dialog was successfully dismissed, ``False`` if none
            of the known "deny" button texts were found.
        """
        deny_texts = ["Deny", "拒绝", "DENY", "Don't allow"]
        for t in deny_texts:
            try:
                self.click(text=t, timeout=1.5)
                return True
            except ElementNotFoundError:
                continue
        return False

    # ─── Toast detection ──────────────────────────────────────────────────────────

    def get_toast_text(self, timeout: float = 5.0) -> str | None:
        """
        Attempt to capture the text of an Android Toast message.

        Toasts are ephemeral; this method waits up to ``timeout`` seconds for
        a ``Toast`` element to appear in the hierarchy.

        Args:
            timeout: Maximum seconds to wait for a Toast (default 5.0).

        Returns:
            The Toast message string, or ``None`` if no Toast appeared within
            the timeout.
        """
        try:
            el = self._wait_for_element(
                AppiumBy.XPATH,
                '//android.widget.Toast',
                timeout=timeout
            )
            return el.get_attribute("name") or el.text
        except (ElementNotFoundError, Exception):
            return None

    def assert_toast(
        self, expected_text: str, timeout: float = 5.0, contains: bool = True
    ) -> None:
        """
        Assert that a Toast message with the expected text appears on screen.

        Args:
            expected_text: The expected text (or substring) in the Toast.
            timeout: Maximum seconds to wait for the Toast (default 5.0).
            contains: If ``True`` (default), checks that ``expected_text`` is a
                substring of the Toast. If ``False``, checks for exact equality.

        Raises:
            AssertionError: If no Toast appears, or if the text does not match.
        """
        toast = self.get_toast_text(timeout=timeout)
        if toast is None:
            raise AssertionError(f"No toast found within {timeout}s (expected: {expected_text!r})")
        if contains and expected_text not in toast:
            raise AssertionError(f"Toast text {toast!r} does not contain {expected_text!r}")
        if not contains and toast != expected_text:
            raise AssertionError(f"Toast text {toast!r} != {expected_text!r}")

    # ─── Screen orientation ───────────────────────────────────────────────────────

    def get_orientation(self) -> str:
        """
        Return the current screen orientation.

        Returns:
            ``"portrait"`` or ``"landscape"`` (lowercase).
        """
        driver = self._require_driver()
        return driver.orientation.lower()

    def set_orientation(self, orientation: str) -> None:
        """
        Set the device screen orientation.

        Args:
            orientation: ``"portrait"`` or ``"landscape"`` (case-insensitive).
        """
        driver = self._require_driver()
        driver.orientation = orientation.upper()

    # ─── Page stability ───────────────────────────────────────────────────────────

    def wait_for_page_stable(
        self, timeout: float | None = None, interval: float = 0.5
    ) -> None:
        """
        Block until the page source stops changing between consecutive polls.

        Useful before asserting on UI state for pages that animate or load
        content dynamically. If the page does not stabilise within ``timeout``,
        the method returns silently (it does not raise).

        Args:
            timeout: Maximum seconds to wait (default: ``self.default_timeout``).
            interval: Seconds between consecutive page-source polls (default 0.5).
        """
        driver = self._require_driver()
        t = timeout or self.default_timeout
        deadline = time.monotonic() + t
        prev_source = ""
        while time.monotonic() < deadline:
            source = driver.page_source
            if source == prev_source:
                return
            prev_source = source
            time.sleep(interval)
