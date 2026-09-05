"""Bounded native-window ownership for the pywebview desktop shell."""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

LOGGER = logging.getLogger(__name__)
MAX_POPPED_OUT_WINDOWS = 4
DEFAULT_POPPED_OUT_WIDTH = 1200
DEFAULT_POPPED_OUT_HEIGHT = 760
MIN_POPPED_OUT_SIZE = (800, 560)


@dataclass(frozen=True, slots=True)
class PopoutKey:
    group_id: str
    window_id: str


class DesktopWindowBridge:
    """JS API and registry for additional top-level WebView windows."""

    def __init__(
        self,
        webview_module: Any,
        frontend_url: str,
        work_area: tuple[int, int, int, int] | None = None,
    ) -> None:
        self._webview = webview_module
        self._frontend_url = frontend_url
        self._work_area = work_area or _virtual_screen_bounds()
        self._main_window: Any | None = None
        self._windows: dict[PopoutKey, Any] = {}
        self._lock = threading.RLock()

    def _set_main_window(self, window: Any) -> None:
        self._main_window = window

    def pop_out_window(
        self,
        group_id: str,
        window_id: str,
        title: str,
        geometry: dict[str, object] | None = None,
    ) -> dict[str, object]:
        key = _validated_key(group_id, window_id)
        safe_title = _validated_title(title)
        with self._lock:
            existing = self._windows.get(key)
            if existing is not None:
                existing.restore()
                existing.show()
                return {"ok": True, "state": "focused"}
            if len(self._windows) >= MAX_POPPED_OUT_WINDOWS:
                return {
                    "ok": False,
                    "state": "limit-reached",
                    "limit": MAX_POPPED_OUT_WINDOWS,
                }
            native_geometry = _clamp_to_work_area(_normalize_geometry(geometry), self._work_area)
            window = self._webview.create_window(
                safe_title,
                _popout_url(self._frontend_url, key),
                width=native_geometry["width"],
                height=native_geometry["height"],
                x=native_geometry.get("x"),
                y=native_geometry.get("y"),
                min_size=MIN_POPPED_OUT_SIZE,
                resizable=True,
                text_select=True,
                js_api=self,
            )
            self._windows[key] = window
            window.events.closed += lambda: self._handle_closed(key, window)
            window.events.resized += lambda width, height: self._notify_geometry(
                key, width=width, height=height
            )
            window.events.moved += lambda x, y: self._notify_geometry(key, x=x, y=y)
            LOGGER.info(
                "desktop_popout_opened group_id=%s window_id=%s count=%d",
                key.group_id,
                key.window_id,
                len(self._windows),
            )
            return {"ok": True, "state": "opened"}

    def dock_window(self, group_id: str, window_id: str) -> dict[str, object]:
        key = _validated_key(group_id, window_id)
        with self._lock:
            window = self._windows.pop(key, None)
        if window is None:
            return {"ok": True, "state": "already-docked"}
        window.destroy()
        LOGGER.info(
            "desktop_popout_docked group_id=%s window_id=%s",
            key.group_id,
            key.window_id,
        )
        return {"ok": True, "state": "docked"}

    def focus_window(self, group_id: str, window_id: str) -> dict[str, object]:
        key = _validated_key(group_id, window_id)
        with self._lock:
            window = self._windows.get(key)
        if window is None:
            return {"ok": False, "state": "not-found"}
        window.restore()
        window.show()
        return {"ok": True, "state": "focused"}

    def _handle_closed(self, key: PopoutKey, expected_window: Any) -> None:
        with self._lock:
            current = self._windows.get(key)
            if current is expected_window:
                self._windows.pop(key, None)
            elif current is None:
                return
            else:
                return
        LOGGER.info(
            "desktop_popout_closed group_id=%s window_id=%s",
            key.group_id,
            key.window_id,
        )
        self._dispatch("stock-harness:native-window-closed", {
            "groupId": key.group_id,
            "windowId": key.window_id,
        })

    def _notify_geometry(self, key: PopoutKey, **geometry: int) -> None:
        self._dispatch("stock-harness:native-window-geometry", {
            "groupId": key.group_id,
            "windowId": key.window_id,
            "geometry": geometry,
        })

    def _dispatch(self, event_name: str, detail: dict[str, object]) -> None:
        script = (
            "window.dispatchEvent(new CustomEvent("
            f"{json.dumps(event_name)},{{detail:{json.dumps(detail)}}}));"
        )
        targets: list[Any] = []
        if self._main_window is not None:
            targets.append(self._main_window)
        with self._lock:
            targets.extend(self._windows.values())
        for target in targets:
            try:
                target.evaluate_js(script)
            except Exception as error:  # Native window may close between snapshot and dispatch.
                LOGGER.debug("desktop_popout_dispatch_skipped error_type=%s", type(error).__name__)


def _validated_key(group_id: str, window_id: str) -> PopoutKey:
    if not _is_safe_identifier(group_id) or not _is_safe_identifier(window_id):
        raise ValueError("invalid pop-out window identity")
    return PopoutKey(group_id=group_id, window_id=window_id)


def _is_safe_identifier(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        return False
    return all(character.isalnum() or character in "-_:" for character in value)


def _validated_title(value: object) -> str:
    if not isinstance(value, str):
        return "StockHarness"
    normalized = " ".join(value.split())[:80]
    return normalized or "StockHarness"


def _normalize_geometry(value: dict[str, object] | None) -> dict[str, int]:
    geometry = value if isinstance(value, dict) else {}
    width = _bounded_int(geometry.get("width"), DEFAULT_POPPED_OUT_WIDTH, 800, 4096)
    height = _bounded_int(geometry.get("height"), DEFAULT_POPPED_OUT_HEIGHT, 560, 2160)
    result = {"width": width, "height": height}
    for coordinate in ("x", "y"):
        raw = geometry.get(coordinate)
        if isinstance(raw, (int, float)) and -32768 <= raw <= 32768:
            result[coordinate] = int(raw)
    return result


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    if not isinstance(value, (int, float)):
        return default
    return max(minimum, min(maximum, int(value)))


def _clamp_to_work_area(
    geometry: dict[str, int],
    work_area: tuple[int, int, int, int],
) -> dict[str, int]:
    left, top, right, bottom = work_area
    available_width = max(MIN_POPPED_OUT_SIZE[0], right - left)
    available_height = max(MIN_POPPED_OUT_SIZE[1], bottom - top)
    width = min(geometry["width"], available_width)
    height = min(geometry["height"], available_height)
    x = geometry.get("x", left + max(0, (available_width - width) // 2))
    y = geometry.get("y", top + max(0, (available_height - height) // 2))
    return {
        "width": width,
        "height": height,
        "x": max(left, min(x, right - width)),
        "y": max(top, min(y, bottom - height)),
    }


def _virtual_screen_bounds() -> tuple[int, int, int, int]:
    if os.name != "nt":
        return (0, 0, 1920, 1080)
    try:
        import ctypes

        user32 = ctypes.windll.user32
        left = int(user32.GetSystemMetrics(76))
        top = int(user32.GetSystemMetrics(77))
        width = int(user32.GetSystemMetrics(78))
        height = int(user32.GetSystemMetrics(79))
        if width > 0 and height > 0:
            return (left, top, left + width, top + height)
    except (AttributeError, OSError):
        pass
    return (0, 0, 1920, 1080)


def _popout_url(frontend_url: str, key: PopoutKey) -> str:
    parts = urlsplit(frontend_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({"popoutGroupId": key.group_id, "popoutWindowId": key.window_id})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
