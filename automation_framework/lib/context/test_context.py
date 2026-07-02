"""
TestContext - the primary interface for business test cases.
Injected via the `context` pytest fixture.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from automation_framework.lib.appium.client import AppiumClient, ElementNotFoundError
from automation_framework.lib.adb.client import ADBClient

if TYPE_CHECKING:
    from automation_framework.lib.artifacts.manager import ArtifactsManager
    from automation_framework.lib.device.info import DeviceInfo
    from automation_framework.lib.utils.config_loader import Config

logger = logging.getLogger(__name__)


class AppController:
    """
    App lifecycle controller, accessible via context.app.

    Combines Appium and ADB operations for high-level app management
    (install, uninstall, start, stop, restart, clear data, permissions).
    """

    def __init__(self, appium: AppiumClient, adb: ADBClient, config: "Config"):
        self._appium = appium
        self._adb = adb
        self._config = config

    @property
    def package(self) -> str:
        """Return the configured app package name."""
        return self._config.app.package

    @property
    def activity(self) -> str:
        """Return the configured app launch activity."""
        return self._config.app.activity

    def install(self, apk_path: str | None = None) -> None:
        """
        Install the app APK onto the device.

        Args:
            apk_path: Path to the APK file. If ``None``, falls back to
                ``config.app.install_path``.

        Raises:
            ValueError: If neither ``apk_path`` nor a configured path is set.
        """
        path = apk_path or self._config.app.install_path
        if not path:
            raise ValueError(
                "No APK path configured. Set app.install_path in config or pass apk_path."
            )
        logger.info("Installing APK: %s", path)
        self._adb.install_apk(path)

    def uninstall(self) -> None:
        """Uninstall the app from the device."""
        logger.info("Uninstalling app: %s", self.package)
        self._adb.uninstall_app(self.package)

    def is_installed(self) -> bool:
        """Return ``True`` if the app is currently installed on the device."""
        return self._adb.is_app_installed(self.package)

    def start(self) -> None:
        """Launch the app via Appium."""
        logger.info("Starting app: %s", self.package)
        self._appium.start_app()

    def stop(self) -> None:
        """Force-stop the app via ADB."""
        logger.info("Stopping app: %s", self.package)
        self._adb.stop_app(self.package)

    def restart(self) -> None:
        """Force-stop then relaunch the app."""
        logger.info("Restarting app: %s", self.package)
        self.stop()
        time.sleep(0.5)
        self.start()

    def clear_data(self) -> None:
        """
        Clear all app data via ``pm clear``.

        Equivalent to going to Settings → App → Clear Data.
        """
        logger.info("Clearing app data: %s", self.package)
        self._adb.clear_app_data(self.package)

    def background(self, seconds: float = 5.0) -> None:
        """
        Send the app to the background and resume it after *seconds*.

        Args:
            seconds: Number of seconds to keep the app backgrounded.
        """
        self._appium.app_background(seconds)

    def grant_all_permissions(self) -> None:
        """Grant common runtime permissions to the app via ADB."""
        logger.info("Granting all permissions for: %s", self.package)
        self._adb.grant_all_permissions(self.package)


class TestContext:
    """
    Primary test context object injected into test functions as the ``context`` fixture.

    ``TestContext`` is the single entry-point for all UI interactions inside a
    business test case.  It wraps :class:`AppiumClient` and :class:`ADBClient`
    and enriches failures with automatic screenshots and page-source dumps so
    that failures are always diagnosable.

    Attributes:
        device_id: The ADB serial of the device under test.
        device_info: Full :class:`DeviceInfo` for the device under test.
        app: An :class:`AppController` for lifecycle operations.

    Example usage::

        def test_login(context):
            context.app.restart()
            context.click(id="com.example.demo:id/btn_login")
            context.input_text(id="com.example.demo:id/et_username", value="user")
            context.assert_text_exists("Home")
    """

    def __init__(
        self,
        appium_client: AppiumClient,
        adb_client: ADBClient,
        config: "Config",
        device_info: "DeviceInfo",
        artifacts: "ArtifactsManager",
    ) -> None:
        self._appium = appium_client
        self._adb = adb_client
        self._config = config
        self._device_info = device_info
        self._artifacts = artifacts
        self._current_case: str | None = None
        self._app = AppController(appium_client, adb_client, config)
        self._screenshot_counter = 0

    # ─── Meta ─────────────────────────────────────────────────────────────────

    @property
    def device_id(self) -> str:
        """ADB serial identifier for the device under test."""
        return self._device_info.device_id

    @property
    def device_info(self) -> "DeviceInfo":
        """Full device information object."""
        return self._device_info

    @property
    def app(self) -> AppController:
        """App lifecycle controller (install / start / stop / restart / clear)."""
        return self._app

    def set_current_case(self, nodeid: str) -> None:
        """
        Set the currently executing test case node ID.

        Called automatically by the ``context`` fixture so that artifacts are
        saved under the correct test-case directory.

        Args:
            nodeid: The pytest node ID (e.g. ``tests/test_login.py::test_login``).
        """
        self._current_case = nodeid
        self._screenshot_counter = 0

    # ─── Element operations ───────────────────────────────────────────────────

    def _on_element_error(self, error: ElementNotFoundError, label: str = "failure") -> str:
        """
        Save a screenshot and page source after an element-not-found error.

        Args:
            error: The original :class:`ElementNotFoundError`.
            label: A short label used as part of the saved file names.

        Returns:
            An enriched error message that includes the paths to the saved
            screenshot and page source, or the original message on secondary
            failures.
        """
        msg = str(error)
        try:
            ss_path = self._save_screenshot(label)
            msg += f" | screenshot: {ss_path}"
        except Exception:
            logger.debug("Could not save failure screenshot", exc_info=True)
        try:
            ps_path = self._save_page_source(label)
            msg += f" | page_source: {ps_path}"
        except Exception:
            logger.debug("Could not save failure page source", exc_info=True)
        return msg

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
        Click an element located by one of the supported strategies.

        Args:
            id: Resource ID (e.g. ``com.example:id/button``).
            text: Exact element text.
            contains_text: Partial element text.
            xpath: XPath expression.
            accessibility_id: Accessibility / content-description.
            class_name: Widget class name.
            uiautomator: Raw UIAutomator2 selector string.
            timeout: Override the default find timeout (seconds).

        Raises:
            ElementNotFoundError: If the element cannot be located; includes
                paths to the auto-saved screenshot and page source.
        """
        try:
            self._appium.click(
                id=id,
                text=text,
                contains_text=contains_text,
                xpath=xpath,
                accessibility_id=accessibility_id,
                class_name=class_name,
                uiautomator=uiautomator,
                timeout=timeout,
            )
        except ElementNotFoundError as e:
            raise ElementNotFoundError(self._on_element_error(e, "click_failure")) from e

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
        Long-press an element.

        Args:
            id: Resource ID.
            text: Exact element text.
            xpath: XPath expression.
            accessibility_id: Accessibility / content-description.
            timeout: Override the default find timeout (seconds).
            duration_ms: How long to hold the press, in milliseconds.

        Raises:
            ElementNotFoundError: If the element cannot be located.
        """
        try:
            self._appium.long_press(
                id=id,
                text=text,
                xpath=xpath,
                accessibility_id=accessibility_id,
                timeout=timeout,
                duration_ms=duration_ms,
            )
        except ElementNotFoundError as e:
            raise ElementNotFoundError(self._on_element_error(e, "long_press_failure")) from e

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
        Double-click an element.

        Args:
            id: Resource ID.
            text: Exact element text.
            xpath: XPath expression.
            accessibility_id: Accessibility / content-description.
            timeout: Override the default find timeout (seconds).

        Raises:
            ElementNotFoundError: If the element cannot be located.
        """
        try:
            self._appium.double_click(
                id=id,
                text=text,
                xpath=xpath,
                accessibility_id=accessibility_id,
                timeout=timeout,
            )
        except ElementNotFoundError as e:
            raise ElementNotFoundError(self._on_element_error(e, "double_click_failure")) from e

    def tap(self, x: int, y: int) -> None:
        """
        Tap at absolute screen coordinates.

        Args:
            x: Horizontal pixel position.
            y: Vertical pixel position.
        """
        self._appium.tap(x, y)

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
        Find an input element and type text into it.

        Args:
            value: The text to type.
            id: Resource ID locator.
            text: Exact text locator.
            contains_text: Partial text locator.
            xpath: XPath locator.
            accessibility_id: Accessibility / content-description locator.
            class_name: Widget class name locator.
            uiautomator: Raw UIAutomator2 selector.
            timeout: Override the default find timeout (seconds).
            clear_first: Clear existing text before typing.

        Raises:
            ElementNotFoundError: If the element cannot be located.
        """
        try:
            self._appium.input_text(
                value=value,
                id=id,
                text=text,
                contains_text=contains_text,
                xpath=xpath,
                accessibility_id=accessibility_id,
                class_name=class_name,
                uiautomator=uiautomator,
                timeout=timeout,
                clear_first=clear_first,
            )
        except ElementNotFoundError as e:
            raise ElementNotFoundError(self._on_element_error(e, "input_failure")) from e

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
        Clear text from an input element.

        Args:
            id: Resource ID locator.
            text: Exact text locator.
            xpath: XPath locator.
            accessibility_id: Accessibility / content-description locator.
            timeout: Override the default find timeout (seconds).
        """
        self._appium.clear_text(
            id=id,
            text=text,
            xpath=xpath,
            accessibility_id=accessibility_id,
            timeout=timeout,
        )

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
    ):
        """
        Find and return a single ``WebElement``.

        Args:
            id: Resource ID locator.
            text: Exact text locator.
            contains_text: Partial text locator.
            xpath: XPath locator.
            accessibility_id: Accessibility / content-description locator.
            class_name: Widget class name locator.
            uiautomator: Raw UIAutomator2 selector.
            timeout: Override the default find timeout (seconds).

        Returns:
            The found ``WebElement``.

        Raises:
            ElementNotFoundError: If the element cannot be located; includes
                paths to the auto-saved screenshot and page source.
        """
        try:
            return self._appium.find(
                id=id,
                text=text,
                contains_text=contains_text,
                xpath=xpath,
                accessibility_id=accessibility_id,
                class_name=class_name,
                uiautomator=uiautomator,
                timeout=timeout,
            )
        except ElementNotFoundError as e:
            raise ElementNotFoundError(self._on_element_error(e, "find_failure")) from e

    def find_all(
        self,
        *,
        id: str | None = None,
        text: str | None = None,
        xpath: str | None = None,
        class_name: str | None = None,
        timeout: float | None = None,
    ) -> list:
        """
        Find all elements matching the given locator.

        Args:
            id: Resource ID locator.
            text: Exact text locator.
            xpath: XPath locator.
            class_name: Widget class name locator.
            timeout: Override the default find timeout (seconds).

        Returns:
            A (possibly empty) list of ``WebElement`` objects.
        """
        return self._appium.find_all(
            id=id,
            text=text,
            xpath=xpath,
            class_name=class_name,
            timeout=timeout,
        )

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
    ):
        """
        Scroll the screen until the target element is found, then return it.

        Args:
            id: Resource ID locator.
            text: Exact text locator.
            contains_text: Partial text locator.
            xpath: XPath locator.
            accessibility_id: Accessibility / content-description locator.
            direction: Scroll direction — ``"vertical"`` or ``"horizontal"``.
            max_swipes: Maximum number of swipes before giving up.
            timeout: Per-swipe wait timeout in seconds.

        Returns:
            The found ``WebElement``.

        Raises:
            ElementNotFoundError: If the element is not found after all swipes.
        """
        try:
            return self._appium.scroll_find(
                id=id,
                text=text,
                contains_text=contains_text,
                xpath=xpath,
                accessibility_id=accessibility_id,
                direction=direction,
                max_swipes=max_swipes,
                timeout=timeout,
            )
        except ElementNotFoundError as e:
            raise ElementNotFoundError(self._on_element_error(e, "scroll_find_failure")) from e

    def wait_for_gone(
        self,
        *,
        id: str | None = None,
        text: str | None = None,
        xpath: str | None = None,
        accessibility_id: str | None = None,
        timeout: float | None = None,
    ) -> bool:
        """
        Wait until the specified element disappears from the screen.

        Args:
            id: Resource ID locator.
            text: Exact text locator.
            xpath: XPath locator.
            accessibility_id: Accessibility / content-description locator.
            timeout: Maximum seconds to wait.

        Returns:
            ``True`` if the element disappeared within the timeout, ``False``
            otherwise.
        """
        return self._appium.wait_for_gone(
            id=id,
            text=text,
            xpath=xpath,
            accessibility_id=accessibility_id,
            timeout=timeout,
        )

    # ─── Swipe / Scroll ───────────────────────────────────────────────────────

    def swipe_up(self, ratio: float = 0.5) -> None:
        """
        Swipe upward (scroll down) by *ratio* of the screen height.

        Args:
            ratio: Fraction of screen height to swipe (0 < ratio ≤ 1).
        """
        self._appium.swipe_up(ratio)

    def swipe_down(self, ratio: float = 0.5) -> None:
        """
        Swipe downward (scroll up) by *ratio* of the screen height.

        Args:
            ratio: Fraction of screen height to swipe (0 < ratio ≤ 1).
        """
        self._appium.swipe_down(ratio)

    def swipe_left(self, ratio: float = 0.5) -> None:
        """
        Swipe left by *ratio* of the screen width.

        Args:
            ratio: Fraction of screen width to swipe (0 < ratio ≤ 1).
        """
        self._appium.swipe_left(ratio)

    def swipe_right(self, ratio: float = 0.5) -> None:
        """
        Swipe right by *ratio* of the screen width.

        Args:
            ratio: Fraction of screen width to swipe (0 < ratio ≤ 1).
        """
        self._appium.swipe_right(ratio)

    # ─── System ───────────────────────────────────────────────────────────────

    def back(self) -> None:
        """Press the Android back button."""
        self._appium.back()

    def home(self) -> None:
        """Press the Android home button."""
        self._appium.home()

    def open_notifications(self) -> None:
        """Pull down the notification shade."""
        self._appium.open_notifications()

    def hide_keyboard(self) -> None:
        """Dismiss the soft keyboard if it is visible."""
        self._appium.hide_keyboard()

    def allow_permission_if_present(self) -> bool:
        """
        Tap the *Allow* button on a system permission dialog if one is present.

        Returns:
            ``True`` if a dialog was detected and dismissed, ``False`` otherwise.
        """
        return self._appium.allow_permission_if_present()

    def deny_permission_if_present(self) -> bool:
        """
        Tap the *Deny* button on a system permission dialog if one is present.

        Returns:
            ``True`` if a dialog was detected and dismissed, ``False`` otherwise.
        """
        return self._appium.deny_permission_if_present()

    def wait_for_page_stable(self, timeout: float | None = None) -> None:
        """
        Block until the page DOM stops changing (idle state).

        Args:
            timeout: Maximum seconds to wait. Uses the driver default if
                ``None``.
        """
        self._appium.wait_for_page_stable(timeout)

    # ─── App shortcuts ────────────────────────────────────────────────────────

    def start_app(self) -> None:
        """Convenience shortcut for :meth:`AppController.start`."""
        self._app.start()

    def stop_app(self) -> None:
        """Convenience shortcut for :meth:`AppController.stop`."""
        self._app.stop()

    def restart_app(self) -> None:
        """Convenience shortcut for :meth:`AppController.restart`."""
        self._app.restart()

    def clear_app(self) -> None:
        """Convenience shortcut for :meth:`AppController.clear_data`."""
        self._app.clear_data()

    # ─── Screenshot & page source ─────────────────────────────────────────────

    def _save_screenshot(self, name: str = "") -> str:
        """
        Capture a screenshot and persist it under the current case's directory.

        Args:
            name: A descriptive label for the file stem.  An auto-incrementing
                counter is used when *name* is empty.

        Returns:
            The absolute path of the saved PNG file as a string.
        """
        self._screenshot_counter += 1
        if not name:
            name = f"step_{self._screenshot_counter:03d}"
        if self._current_case:
            path: Path = (
                self._artifacts.case_screenshots_dir(self.device_id, self._current_case)
                / f"{name}.png"
            )
        else:
            path = (
                self._artifacts.device_dir(self.device_id) / "screenshots" / f"{name}.png"
            )
        return self._appium.take_screenshot(path)

    def _save_page_source(self, name: str = "page_source") -> str:
        """
        Dump the current page source XML and persist it.

        Args:
            name: A descriptive label for the file stem.

        Returns:
            The absolute path of the saved XML file as a string.
        """
        if self._current_case:
            path: Path = (
                self._artifacts.case_page_source_dir(self.device_id, self._current_case)
                / f"{name}.xml"
            )
        else:
            path = self._artifacts.device_dir(self.device_id) / f"{name}.xml"
        return self._appium.save_page_source(path)

    def screenshot(self, name: str = "") -> str:
        """
        Take a named screenshot from a test case.

        Args:
            name: Optional label for the file.  Auto-incremented if omitted.

        Returns:
            Absolute path of the saved PNG file.
        """
        return self._save_screenshot(name)

    def snapshot_before_assert(self) -> str:
        """
        Take a screenshot labelled ``pre_assert`` before running an assertion.

        Useful for manual debugging when an assertion is about to be made.

        Returns:
            Absolute path of the saved PNG file.
        """
        return self._save_screenshot("pre_assert")

    # ─── Assertions ───────────────────────────────────────────────────────────

    def assert_text_exists(
        self, expected_text: str, timeout: float | None = None
    ) -> None:
        """
        Assert that an element with *exactly* the given text is visible.

        On failure, a screenshot and page source are saved automatically, and
        their paths are included in the error message.

        Args:
            expected_text: The exact text the element must show.
            timeout: Override the default find timeout (seconds).

        Raises:
            AssertionError: If no element with that text is found.
        """
        try:
            self._appium.find(text=expected_text, timeout=timeout)
        except ElementNotFoundError:
            ss_path = None
            ps_path = None
            try:
                ss_path = self._save_screenshot("assert_failure")
            except Exception:
                logger.debug("Could not save assert_failure screenshot", exc_info=True)
            try:
                ps_path = self._save_page_source("assert_failure")
            except Exception:
                logger.debug("Could not save assert_failure page source", exc_info=True)
            detail = ""
            if ss_path:
                detail += f" | screenshot: {ss_path}"
            if ps_path:
                detail += f" | page_source: {ps_path}"
            raise AssertionError(
                f"Expected text not found on screen: {expected_text!r}{detail}"
            )

    def assert_element_exists(
        self,
        *,
        id: str | None = None,
        text: str | None = None,
        contains_text: str | None = None,
        xpath: str | None = None,
        accessibility_id: str | None = None,
        timeout: float | None = None,
    ) -> None:
        """
        Assert that an element matching the given locator is visible.

        On failure, a screenshot and page source are saved automatically.

        Args:
            id: Resource ID locator.
            text: Exact text locator.
            contains_text: Partial text locator.
            xpath: XPath locator.
            accessibility_id: Accessibility / content-description locator.
            timeout: Override the default find timeout (seconds).

        Raises:
            AssertionError: If no matching element is found.
        """
        try:
            self._appium.find(
                id=id,
                text=text,
                contains_text=contains_text,
                xpath=xpath,
                accessibility_id=accessibility_id,
                timeout=timeout,
            )
        except ElementNotFoundError:
            ss_path = None
            ps_path = None
            try:
                ss_path = self._save_screenshot("assert_failure")
            except Exception:
                logger.debug("Could not save assert_failure screenshot", exc_info=True)
            try:
                ps_path = self._save_page_source("assert_failure")
            except Exception:
                logger.debug("Could not save assert_failure page source", exc_info=True)
            detail = ""
            if ss_path:
                detail += f" | screenshot: {ss_path}"
            if ps_path:
                detail += f" | page_source: {ps_path}"
            raise AssertionError(
                f"Element not found: id={id!r}, text={text!r}, "
                f"contains_text={contains_text!r}, xpath={xpath!r}, "
                f"accessibility_id={accessibility_id!r}{detail}"
            )

    def assert_element_not_exists(
        self,
        *,
        id: str | None = None,
        text: str | None = None,
        contains_text: str | None = None,
        xpath: str | None = None,
        accessibility_id: str | None = None,
        timeout: float | None = None,
    ) -> None:
        """
        Assert that an element matching the given locator is NOT visible.

        Uses a short poll (default 2 s) to confirm absence before failing.

        Args:
            id: Resource ID locator.
            text: Exact text locator.
            contains_text: Partial text locator.
            xpath: XPath locator.
            accessibility_id: Accessibility / content-description locator.
            timeout: Override the short-poll timeout (seconds, default 2).

        Raises:
            AssertionError: If the element is unexpectedly present.
        """
        t = timeout if timeout is not None else 2.0
        present = self._appium.is_present(
            id=id,
            text=text,
            contains_text=contains_text,
            xpath=xpath,
            accessibility_id=accessibility_id,
            timeout=t,
        )
        if present:
            ss_path = None
            try:
                ss_path = self._save_screenshot("unexpected_element")
            except Exception:
                logger.debug("Could not save unexpected_element screenshot", exc_info=True)
            detail = f" | screenshot: {ss_path}" if ss_path else ""
            raise AssertionError(
                f"Element unexpectedly found: id={id!r}, text={text!r}, "
                f"contains_text={contains_text!r}, xpath={xpath!r}{detail}"
            )

    def assert_contains_text(
        self, partial_text: str, timeout: float | None = None
    ) -> None:
        """
        Assert that any element on screen contains *partial_text*.

        Args:
            partial_text: The substring to search for across all visible elements.
            timeout: Override the default find timeout (seconds).

        Raises:
            AssertionError: If no element containing the text is found.
        """
        try:
            self._appium.find(contains_text=partial_text, timeout=timeout)
        except ElementNotFoundError:
            ss_path = None
            try:
                ss_path = self._save_screenshot("assert_failure")
            except Exception:
                logger.debug("Could not save assert_failure screenshot", exc_info=True)
            detail = f" | screenshot: {ss_path}" if ss_path else ""
            raise AssertionError(
                f"No element found containing text: {partial_text!r}{detail}"
            )

    # ─── Logcat ───────────────────────────────────────────────────────────────

    def get_logcat_file(self) -> Path:
        """
        Return the :class:`~pathlib.Path` to the device's live logcat file.

        The file is managed by the :class:`ArtifactsManager` and is written
        continuously during a test session.
        """
        return self._artifacts.logcat_raw_path(self.device_id)

    def search_log(self, keyword: str, lines: int = 200) -> list[str]:
        """
        Search recent logcat output for lines containing *keyword*.

        Args:
            keyword: The substring to look for in each logcat line.
            lines: How many recent logcat lines to inspect.

        Returns:
            A list of matching log lines (may be empty).
        """
        snapshot = self._adb.get_logcat_snapshot(lines=lines)
        return [line for line in snapshot.split("\n") if keyword in line]

    # ─── ADB extension ────────────────────────────────────────────────────────

    def run_adb(self, *args: str, timeout: int = 30) -> tuple[int, str, str]:
        """
        Execute a custom ADB shell command on the test device.

        Example::

            rc, out, err = context.run_adb("shell", "getprop", "ro.build.version.release")

        Args:
            *args: Arguments passed directly to the ADB client after the
                device serial flag.
            timeout: Maximum time to wait for the command, in seconds.

        Returns:
            A 3-tuple of ``(returncode, stdout, stderr)``.
        """
        return self._adb.run_custom(*args, timeout=timeout)
