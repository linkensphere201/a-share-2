from pathlib import Path

from stock_harness.desktop import (
    DEFAULT_DESKTOP_PORT,
    _parser,
    open_desktop_window,
    resolve_log_directory,
    resolve_runtime_log_directory,
    resolve_webview_storage_path,
)


class FakeWebview:
    def __init__(self) -> None:
        self.window_args = ()
        self.window_kwargs = {}
        self.start_kwargs = {}

    def create_window(self, *args, **kwargs) -> None:
        self.window_args = args
        self.window_kwargs = kwargs

    def start(self, **kwargs) -> None:
        self.start_kwargs = kwargs


def test_desktop_defaults_to_stable_origin() -> None:
    args = _parser().parse_args([])

    assert args.host == "127.0.0.1"
    assert args.port == DEFAULT_DESKTOP_PORT == 8765


def test_webview_uses_persistent_profile(tmp_path: Path) -> None:
    webview = FakeWebview()
    storage_path = tmp_path / "webview"

    open_desktop_window(webview, "http://127.0.0.1:8765", False, storage_path)

    assert webview.window_args[:2] == ("StockHarness", "http://127.0.0.1:8765")
    assert storage_path.is_dir()
    assert webview.start_kwargs == {
        "gui": "edgechromium",
        "debug": False,
        "private_mode": False,
        "storage_path": str(storage_path),
    }


def test_webview_storage_prefers_local_app_data(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert resolve_webview_storage_path(Path("unused")) == (
        tmp_path.resolve() / "StockHarness" / "WebView"
    )
    assert resolve_log_directory(Path("unused")) == (
        tmp_path.resolve() / "StockHarness" / "logs"
    )


def test_smoke_test_uses_independent_log_directory(tmp_path: Path) -> None:
    assert resolve_runtime_log_directory(tmp_path, False) == tmp_path
    assert resolve_runtime_log_directory(tmp_path, True) == tmp_path / "smoke"
