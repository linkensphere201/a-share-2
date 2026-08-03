"""Windows desktop launcher for the local StockHarness service and web UI."""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import uvicorn

from stock_harness.api import create_app
from stock_harness.auto_update import AutoUpdateService
from stock_harness.config import load_runtime_settings


@dataclass(frozen=True, slots=True)
class DesktopPaths:
    provider_config: Path
    storage_config: Path
    web_dist: Path


class DesktopServer:
    def __init__(self, app: object, host: str, port: int) -> None:
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=host,
                port=port,
                log_config=None,
                access_log=False,
            )
        )
        self._thread = threading.Thread(target=self._server.run, name="stock-harness-api", daemon=True)

    def start(self, health_url: str, timeout_seconds: float = 30.0) -> None:
        self._thread.start()
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if not self._thread.is_alive():
                raise RuntimeError("StockHarness backend stopped during startup")
            try:
                with urllib.request.urlopen(health_url, timeout=1.0) as response:
                    if response.status == 200:
                        return
            except (OSError, urllib.error.URLError):
                time.sleep(0.1)
        self.stop()
        raise TimeoutError(f"StockHarness backend did not become ready: {health_url}")

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(timeout=10.0)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = resolve_desktop_paths(args.provider_config, args.storage_config, args.web_dist)
    port = args.port or find_available_port(args.host)
    url = f"http://{args.host}:{port}"
    settings = load_runtime_settings(paths.provider_config, paths.storage_config)
    update_service = (
        AutoUpdateService.from_config(paths.provider_config, paths.storage_config)
        if settings.auto_update.enabled and not args.no_auto_update and not args.smoke_test
        else None
    )
    app = create_app(
        provider_config=paths.provider_config,
        storage_config=paths.storage_config,
        web_dist=paths.web_dist,
        update_status=update_service.status if update_service else None,
    )
    server = DesktopServer(app, args.host, port)
    server.start(f"{url}/api/health")
    if update_service is not None:
        update_service.start()
    try:
        if args.smoke_test:
            with urllib.request.urlopen(url, timeout=5.0) as response:
                if response.status != 200 or b"StockHarness" not in response.read():
                    raise RuntimeError("StockHarness frontend smoke test failed")
            return 0
        import webview

        webview.create_window(
            "StockHarness",
            url,
            width=1440,
            height=900,
            min_size=(960, 640),
            text_select=True,
        )
        webview.start(gui="edgechromium", debug=args.debug)
    finally:
        if update_service is not None:
            update_service.stop()
        server.stop()
    return 0


def resolve_desktop_paths(
    provider_config: str | None = None,
    storage_config: str | None = None,
    web_dist: str | None = None,
) -> DesktopPaths:
    install_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else _project_root()
    resource_root = Path(getattr(sys, "_MEIPASS", install_root))
    config_root = install_root / "config"
    if getattr(sys, "frozen", False):
        checkout_root = install_root.parents[1]
        if (
            (checkout_root / "pyproject.toml").is_file()
            and (checkout_root / "config" / "providers.local.yaml").is_file()
            and (checkout_root / "config" / "storage.local.yaml").is_file()
        ):
            config_root = checkout_root / "config"
    paths = DesktopPaths(
        provider_config=Path(provider_config).resolve() if provider_config else config_root / "providers.local.yaml",
        storage_config=Path(storage_config).resolve() if storage_config else config_root / "storage.local.yaml",
        web_dist=Path(web_dist).resolve() if web_dist else resource_root / ("web" if getattr(sys, "frozen", False) else "web/dist"),
    )
    for label, path in (
        ("provider configuration", paths.provider_config),
        ("storage configuration", paths.storage_config),
        ("frontend build", paths.web_dist / "index.html"),
    ):
        if not path.exists():
            raise FileNotFoundError(f"{label} not found: {path}")
    return paths


def find_available_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch the StockHarness desktop application")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--provider-config")
    parser.add_argument("--storage-config")
    parser.add_argument("--web-dist")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--no-auto-update", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
