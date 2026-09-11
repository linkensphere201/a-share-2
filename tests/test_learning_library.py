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


def test_learning_chat_context_is_bounded_to_visible_course_page(tmp_path: Path) -> None:
    _create_library(tmp_path)
    page = tmp_path / "systems" / "trend-genggui" / "site" / "episode.html"
    page.write_text(
        "<html><head><style>.hidden{}</style><script>secret()</script></head>"
        "<body><h1>突破与回踩</h1><p>等待结构确认，不引入未来信息。</p></body></html>",
        encoding="utf-8",
    )
    library = LearningLibrary(tmp_path)

    context = library.build_chat_context(
        "trend-genggui",
        asset_path="systems/trend-genggui/site/episode.html",
        page_title="突破课程",
    )

    assert context["context_kind"] == "learning_system"
    assert context["context_id"] == "trend-genggui"
    assert context["page"]["title"] == "突破课程"
    assert "突破与回踩" in context["page"]["content"]
    assert "secret" not in context["page"]["content"]
    with pytest.raises(LearningLibraryError, match="selected course"):
        library.build_chat_context("trend-genggui", asset_path="catalog.json")


def test_learning_chat_conversation_is_persisted_by_course(tmp_path: Path) -> None:
    _create_library(tmp_path)
    store = SQLiteMarketDataStore(":memory:")
    app = create_app(store=store, learning_root=tmp_path)

    with TestClient(app) as client:
        created = client.post("/api/ai/conversations", json={
            "context_kind": "learning_system", "context_id": "trend-genggui",
        })
        reused = client.post("/api/ai/conversations", json={
            "context_kind": "learning_system", "context_id": "trend-genggui",
        })
        listed = client.get(
            "/api/ai/conversations?context_kind=learning_system"
            "&context_id=trend-genggui"
        )

    store.close()
    assert created.status_code == 201
    assert reused.json()["conversation_id"] == created.json()["conversation_id"]
    assert created.json()["context_kind"] == "learning_system"
    assert listed.json()["items"][0]["context_id"] == "trend-genggui"
