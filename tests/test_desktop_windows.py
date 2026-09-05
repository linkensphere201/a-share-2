from stock_harness.desktop_windows import (
    MAX_POPPED_OUT_WINDOWS,
    DesktopWindowBridge,
)


class FakeEvent:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def emit(self, *args) -> None:
        for handler in list(self.handlers):
            handler(*args)


class FakeEvents:
    def __init__(self) -> None:
        self.closed = FakeEvent()
        self.resized = FakeEvent()
        self.moved = FakeEvent()


class FakeWindow:
    def __init__(self) -> None:
        self.events = FakeEvents()
        self.destroyed = False
        self.restored = 0
        self.shown = 0
        self.scripts = []

    def destroy(self) -> None:
        self.destroyed = True
        self.events.closed.emit()

    def restore(self) -> None:
        self.restored += 1

    def show(self) -> None:
        self.shown += 1

    def evaluate_js(self, script: str) -> None:
        self.scripts.append(script)


class FakeWebview:
    def __init__(self) -> None:
        self.calls = []

    def create_window(self, *args, **kwargs):
        window = FakeWindow()
        self.calls.append((args, kwargs, window))
        return window


def test_popout_registry_opens_once_and_focuses_duplicate() -> None:
    webview = FakeWebview()
    bridge = DesktopWindowBridge(webview, "http://127.0.0.1:8765/?v=abc", (0, 0, 1920, 1080))

    opened = bridge.pop_out_window(
        "group-primary", "chart-primary", "StockHarness - Test",
        {"x": 20, "y": 30, "width": 1000, "height": 700},
    )
    focused = bridge.pop_out_window("group-primary", "chart-primary", "ignored")

    assert opened == {"ok": True, "state": "opened"}
    assert focused == {"ok": True, "state": "focused"}
    assert len(webview.calls) == 1
    args, kwargs, window = webview.calls[0]
    assert "popoutGroupId=group-primary" in args[1]
    assert "popoutWindowId=chart-primary" in args[1]
    assert kwargs["width"] == 1000
    assert kwargs["height"] == 700
    assert window.restored == window.shown == 1


def test_native_close_dispatches_dock_event_to_main_window() -> None:
    webview = FakeWebview()
    bridge = DesktopWindowBridge(webview, "http://127.0.0.1:8765/", (0, 0, 1920, 1080))
    main = FakeWindow()
    bridge._set_main_window(main)
    bridge.pop_out_window("group-primary", "list-primary", "List")
    child = webview.calls[0][2]

    child.events.closed.emit()

    assert any("stock-harness:native-window-closed" in script for script in main.scripts)
    assert bridge.focus_window("group-primary", "list-primary") == {
        "ok": False, "state": "not-found",
    }


def test_explicit_dock_is_idempotent_and_does_not_dispatch_close() -> None:
    webview = FakeWebview()
    bridge = DesktopWindowBridge(webview, "http://127.0.0.1:8765/", (0, 0, 1920, 1080))
    main = FakeWindow()
    bridge._set_main_window(main)
    bridge.pop_out_window("group-primary", "chart-primary", "Chart")

    assert bridge.dock_window("group-primary", "chart-primary")["state"] == "docked"
    assert bridge.dock_window("group-primary", "chart-primary")["state"] == "already-docked"
    assert main.scripts == []


def test_popout_registry_enforces_resource_limit() -> None:
    webview = FakeWebview()
    bridge = DesktopWindowBridge(webview, "http://127.0.0.1:8765/", (0, 0, 1920, 1080))
    for index in range(MAX_POPPED_OUT_WINDOWS):
        assert bridge.pop_out_window("group", f"chart-{index}", "Chart")["ok"] is True

    rejected = bridge.pop_out_window("group", "chart-overflow", "Chart")

    assert rejected == {
        "ok": False, "state": "limit-reached", "limit": MAX_POPPED_OUT_WINDOWS,
    }


def test_geometry_events_are_bounded_and_dispatched() -> None:
    webview = FakeWebview()
    bridge = DesktopWindowBridge(webview, "http://127.0.0.1:8765/", (0, 0, 1920, 1080))
    main = FakeWindow()
    bridge._set_main_window(main)
    bridge.pop_out_window("group", "chart", "Chart", {"width": 10, "height": 99999})
    _, kwargs, child = webview.calls[0]

    child.events.resized.emit(1300, 800)
    child.events.moved.emit(100, 120)

    assert kwargs["width"] == 800
    assert kwargs["height"] == 1080
    assert kwargs["x"] == 560
    assert kwargs["y"] == 0
    assert sum("native-window-geometry" in script for script in main.scripts) == 2
