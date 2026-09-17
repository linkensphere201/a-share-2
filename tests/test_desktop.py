from pathlib import Path
from types import SimpleNamespace

import pytest

from stock_harness.desktop import (
    DEFAULT_DESKTOP_PORT,
    DesktopServer,
    _parser,
    build_frontend_url,
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

    def create_window(self, *args, **kwargs):
        self.window_args = args
        self.window_kwargs = kwargs
        return object()

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
    assert webview.window_kwargs["js_api"] is not None
    assert storage_path.is_dir()
    assert webview.start_kwargs == {
        "gui": "edgechromium",
        "debug": False,
        "private_mode": False,
        "storage_path": str(storage_path),
    }


def test_frontend_url_changes_with_built_index(tmp_path: Path) -> None:
    index_file = tmp_path / "index.html"
    index_file.write_text("first build", encoding="utf-8")
    first = build_frontend_url("http://127.0.0.1:8765", index_file)

    index_file.write_text("second build", encoding="utf-8")
    second = build_frontend_url("http://127.0.0.1:8765/", index_file)

    assert first.startswith("http://127.0.0.1:8765/?v=")
    assert second.startswith("http://127.0.0.1:8765/?v=")
    assert first != second


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


def test_backend_readiness_does_not_depend_on_http_proxy(monkeypatch):
    import urllib.request
    from fastapi import FastAPI

    def forbidden(*args, **kwargs):
        pytest.fail("startup must use owned server readiness, not an HTTP self-probe")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    app = FastAPI()
    server = DesktopServer(app, "127.0.0.1", 0)
    try:
        server.start(timeout_seconds=5)
        assert server._server.started
        sockets = server._server.servers[0].sockets
        port = sockets[0].getsockname()[1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/openapi.json", timeout=2) as response:
            assert response.status == 200
    finally:
        server.stop()
    assert not server._thread.is_alive()


@pytest.mark.parametrize("alive", [False, True])
def test_backend_startup_failure_and_timeout_are_not_ready(alive):
    server = DesktopServer.__new__(DesktopServer)
    joined = []
    server._server = SimpleNamespace(started=False, should_exit=False)
    server._thread = SimpleNamespace(start=lambda: None, is_alive=lambda: alive,
                                     join=lambda timeout: joined.append(timeout))
    with pytest.raises(TimeoutError if alive else RuntimeError):
        server.start(timeout_seconds=.01)
    if alive:
        assert server._server.should_exit
        assert joined == [10.0]
