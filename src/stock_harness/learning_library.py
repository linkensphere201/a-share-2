"""Local trading-system course catalog and bounded media intake."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from pathlib import Path
import re
from typing import BinaryIO


CATALOG_SCHEMA_VERSION = "1.0"
MANIFEST_SCHEMA_VERSION = "1.0"
SYSTEM_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PUBLIC_SUFFIXES = {
    ".html", ".css", ".js", ".json", ".jpg", ".jpeg", ".png", ".webp",
    ".vtt", ".mp4", ".webm", ".mkv", ".m4a",
}


class LearningLibraryError(RuntimeError):
    pass


class LearningLibrary:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()

    def list_systems(self) -> list[dict[str, object]]:
        catalog = self._catalog()
        result = []
        for raw in catalog.get("systems", []):
            if not isinstance(raw, dict):
                continue
            try:
                system = self._validated_system(raw)
                index = self.resolve_public_file(str(system["index_path"]))
            except (LearningLibraryError, FileNotFoundError):
                continue
            result.append({**system, "available": index.is_file()})
        return result

    def get_system(self, system_id: str) -> dict[str, object] | None:
        normalized = validate_system_id(system_id)
        return next(
            (item for item in self.list_systems() if item["system_id"] == normalized),
            None,
        )

    def resolve_public_file(self, relative_path: str) -> Path:
        value = relative_path.replace("\\", "/").strip("/")
        if not value or "\x00" in value:
            raise LearningLibraryError("learning asset path is required")
        candidate = (self.root / value).resolve()
        if not candidate.is_relative_to(self.root):
            raise LearningLibraryError("learning asset path escapes the library root")
        if candidate.suffix.lower() not in PUBLIC_SUFFIXES:
            raise LearningLibraryError("learning asset type is not allowed")
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        return candidate

    def _catalog(self) -> dict[str, object]:
        path = self.root / "catalog.json"
        if not path.is_file():
            return {"schema_version": CATALOG_SCHEMA_VERSION, "systems": []}
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or payload.get("schema_version") != CATALOG_SCHEMA_VERSION:
            raise LearningLibraryError("unsupported learning catalog schema")
        if not isinstance(payload.get("systems"), list):
            raise LearningLibraryError("learning catalog systems must be a list")
        return payload

    def _validated_system(self, raw: dict[str, object]) -> dict[str, object]:
        system_id = validate_system_id(str(raw.get("system_id") or ""))
        title = str(raw.get("title") or "").strip()
        index_path = str(raw.get("index_path") or "").replace("\\", "/").strip("/")
        expected_prefix = f"systems/{system_id}/site/"
        if not title or not index_path.startswith(expected_prefix):
            raise LearningLibraryError("invalid learning system title or index path")
        return {
            "system_id": system_id,
            "title": title,
            "methodology": str(raw.get("methodology") or "").strip(),
            "status": str(raw.get("status") or "published"),
            "default": bool(raw.get("default")),
            "index_path": index_path,
            "corpus_version": str(raw.get("corpus_version") or ""),
            "publication_version": str(raw.get("publication_version") or ""),
        }


def validate_system_id(value: str) -> str:
    normalized = value.strip().lower()
    if not SYSTEM_ID_PATTERN.fullmatch(normalized):
        raise LearningLibraryError("invalid trading-system identifier")
    return normalized


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        _consume(source, digest.update, chunk_size)
    return digest.hexdigest()


def copy_verified_media(
    source: Path, destination: Path, *, chunk_size: int = 8 * 1024 * 1024,
) -> dict[str, object]:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_digest = sha256_file(source, chunk_size)
    if destination.is_file():
        if destination.stat().st_size != source.stat().st_size:
            raise LearningLibraryError(f"media destination conflicts: {destination}")
        if sha256_file(destination, chunk_size) != source_digest:
            raise LearningLibraryError(f"media destination checksum conflicts: {destination}")
        return {"bytes": destination.stat().st_size, "sha256": source_digest, "copied": False}
    partial = destination.with_suffix(destination.suffix + ".partial")
    digest = hashlib.sha256()
    with source.open("rb") as input_file, partial.open("wb") as output_file:
        while chunk := input_file.read(chunk_size):
            output_file.write(chunk)
            digest.update(chunk)
    if digest.hexdigest() != source_digest or partial.stat().st_size != source.stat().st_size:
        raise LearningLibraryError(f"media copy verification failed: {destination}")
    partial.replace(destination)
    return {"bytes": destination.stat().st_size, "sha256": source_digest, "copied": True}


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n",
    )


def _consume(
    source: BinaryIO, callback: Callable[[bytes], object], chunk_size: int,
) -> None:
    while chunk := source.read(chunk_size):
        callback(chunk)
