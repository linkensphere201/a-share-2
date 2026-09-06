from pathlib import Path

import pytest

from stock_harness.learning_library import LearningLibraryError
from stock_harness.learning_media_migration import (
    archive_source_media,
    delete_verified_sources,
)


def test_archive_reuses_existing_media_and_preserves_unique_sources(tmp_path: Path) -> None:
    system = tmp_path / "library" / "systems" / "test-system"
    media = system / "media"
    media.mkdir(parents=True)
    (media / "published.mp4").write_bytes(b"published")
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "duplicate.mp4").write_bytes(b"published")
    (source / "nested" / "audio.m4a").write_bytes(b"audio")
    receipt = system / "source-migration-receipt.json"

    result = archive_source_media(system, {"source": source}, receipt)

    assert len(result["items"]) == 2
    destinations = {item["relative_source"]: item["destination"] for item in result["items"]}
    assert destinations["duplicate.mp4"] == "media/published.mp4"
    assert destinations["nested/audio.m4a"] == "source-archive/source/nested/audio.m4a"
    assert (source / "duplicate.mp4").is_file()
    assert receipt.is_file()


def test_delete_preflights_every_file_before_removing_sources(tmp_path: Path) -> None:
    system = tmp_path / "library" / "systems" / "test-system"
    source = tmp_path / "source"
    source.mkdir(parents=True)
    first = source / "first.mp4"
    second = source / "second.m4a"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    receipt = system / "source-migration-receipt.json"
    archive_source_media(system, {"source": source}, receipt)
    archived_second = system / "source-archive" / "source" / "second.m4a"
    archived_second.write_bytes(b"tampered")

    with pytest.raises(LearningLibraryError, match="destination size changed"):
        delete_verified_sources(system, {"source": source}, receipt)

    assert first.is_file()
    assert second.is_file()


def test_delete_removes_only_verified_media_files(tmp_path: Path) -> None:
    system = tmp_path / "library" / "systems" / "test-system"
    source = tmp_path / "source"
    source.mkdir(parents=True)
    media = source / "episode.mp4"
    note = source / "keep.txt"
    media.write_bytes(b"episode")
    note.write_text("keep", encoding="utf-8")
    receipt = system / "source-migration-receipt.json"
    archive_source_media(system, {"source": source}, receipt)

    result = delete_verified_sources(system, {"source": source}, receipt)

    assert result["state"] == "sources-deleted"
    assert result["deleted_count"] == 1
    assert not media.exists()
    assert note.is_file()
    assert source.is_dir()
