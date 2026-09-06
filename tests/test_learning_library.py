from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from stock_harness.api import create_app
from stock_harness.learning_library import (
    LearningLibrary,
    LearningLibraryError,
    copy_verified_media,
    write_json,
)
from stock_harness.sqlite_store import SQLiteMarketDataStore


def _create_library(root: Path) -> None:
    site = root / "systems" / "trend-genggui" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<h1>course</h1>", encoding="utf-8")
    media = root / "systems" / "trend-genggui" / "media"
    media.mkdir()
    (media / "episode.mp4").write_bytes(b"0123456789")
    write_json(root / "catalog.json", {
        "schema_version": "1.0",
        "systems": [{
            "system_id": "trend-genggui",
            "title": "趋势交易体系",
            "methodology": "trend-trading",
            "status": "published",
            "default": True,
            "corpus_version": "test-v1",
            "publication_version": "test-v1",
            "index_path": "systems/trend-genggui/site/index.html",
        }],
    })


def test_learning_library_lists_only_published_files_and_blocks_escape(tmp_path: Path) -> None:
    _create_library(tmp_path)
    library = LearningLibrary(tmp_path)

    assert library.list_systems()[0]["system_id"] == "trend-genggui"
    assert library.resolve_public_file(
        "systems/trend-genggui/site/index.html"
    ).is_file()
    with pytest.raises(LearningLibraryError, match="escapes"):
        library.resolve_public_file("../outside.html")
    with pytest.raises(LearningLibraryError, match="not allowed"):
        library.resolve_public_file("catalog.txt")


def test_copy_verified_media_is_idempotent_and_rejects_conflicts(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    destination = tmp_path / "library" / "episode.mp4"
    source.write_bytes(b"video-content")

    first = copy_verified_media(source, destination, chunk_size=4)
    second = copy_verified_media(source, destination, chunk_size=4)

    assert first["copied"] is True
    assert second["copied"] is False
    destination.write_bytes(b"different")
    with pytest.raises(LearningLibraryError, match="conflicts"):
        copy_verified_media(source, destination, chunk_size=4)


def test_learning_api_opens_loopback_course_and_serves_assets(tmp_path: Path) -> None:
    _create_library(tmp_path)
    opened: list[str] = []
    store = SQLiteMarketDataStore(":memory:")
    app = create_app(
        store=store,
        learning_root=tmp_path,
        learning_browser_opener=lambda url: opened.append(url) is None,
    )

    with TestClient(app) as client:
        catalog = client.get("/api/learning/systems")
        opened_response = client.post("/api/learning/systems/trend-genggui/open")
        page = client.get("/learning/systems/trend-genggui/site/index.html")
        media = client.get(
            "/learning/systems/trend-genggui/media/episode.mp4",
            headers={"Range": "bytes=2-5"},
        )
        missing = client.post("/api/learning/systems/not-published/open")

    store.close()
    assert catalog.json()["items"][0]["available"] is True
    assert opened_response.json()["status"] == "opened"
    assert opened == [
        "http://testserver/learning/systems/trend-genggui/site/index.html"
    ]
    assert page.text == "<h1>course</h1>"
    assert media.status_code == 206
    assert media.content == b"2345"
    assert media.headers["content-range"] == "bytes 2-5/10"
    assert missing.status_code == 404
