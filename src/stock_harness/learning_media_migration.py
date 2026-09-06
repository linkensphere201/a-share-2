"""Verified two-phase migration for local trading-system source media."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from stock_harness.learning_library import (
    LearningLibraryError,
    copy_verified_media,
    sha256_file,
    write_json,
)


MEDIA_SUFFIXES = {".mp4", ".m4a", ".mkv", ".webm", ".mov", ".avi"}
RECEIPT_SCHEMA_VERSION = "1.0"


def archive_source_media(
    system_root: Path,
    source_roots: dict[str, Path],
    receipt_path: Path,
) -> dict[str, object]:
    system_root = system_root.resolve()
    roots = _validated_roots(system_root, source_roots)
    digest_index = _index_formal_media(system_root)
    items: list[dict[str, object]] = []

    for label, root in sorted(roots.items()):
        for source in _media_files(root):
            relative_source = source.relative_to(root)
            digest = sha256_file(source)
            destinations = digest_index.get((source.stat().st_size, digest), [])
            if destinations:
                destination = destinations[0]
                copied = False
            else:
                destination = system_root / "source-archive" / label / relative_source
                result = copy_verified_media(source, destination)
                copied = bool(result["copied"])
                digest_index.setdefault((source.stat().st_size, digest), []).append(destination)
            items.append({
                "source_root": label,
                "relative_source": relative_source.as_posix(),
                "destination": destination.relative_to(system_root).as_posix(),
                "bytes": source.stat().st_size,
                "sha256": digest,
                "copied": copied,
                "deleted": False,
            })

    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "created_at": _utc_now(),
        "system_id": system_root.name,
        "state": "verified",
        "items": items,
    }
    write_json(receipt_path.resolve(), receipt)
    return receipt


def delete_verified_sources(
    system_root: Path,
    source_roots: dict[str, Path],
    receipt_path: Path,
) -> dict[str, object]:
    system_root = system_root.resolve()
    roots = _validated_roots(system_root, source_roots)
    receipt_path = receipt_path.resolve()
    receipt = _read_receipt(receipt_path)
    planned: list[tuple[dict[str, object], Path]] = []

    for raw in receipt["items"]:
        item = dict(raw)
        label = str(item.get("source_root") or "")
        if label not in roots:
            raise LearningLibraryError(f"unknown source root in receipt: {label}")
        source = (roots[label] / str(item.get("relative_source") or "")).resolve()
        destination = (system_root / str(item.get("destination") or "")).resolve()
        if not source.is_relative_to(roots[label]):
            raise LearningLibraryError("receipt source escapes its approved root")
        if not destination.is_relative_to(system_root):
            raise LearningLibraryError("receipt destination escapes the learning system")
        if not source.is_file():
            raise LearningLibraryError(f"source missing before delete preflight: {source}")
        _verify_file(source, item, "source")
        _verify_file(destination, item, "destination")
        planned.append((raw, source))

    for item, source in planned:
        source.unlink()
        item["deleted"] = True

    receipt["state"] = "sources-deleted"
    receipt["sources_deleted_at"] = _utc_now()
    receipt["deleted_count"] = len(planned)
    write_json(receipt_path, receipt)
    return receipt


def _validated_roots(system_root: Path, source_roots: dict[str, Path]) -> dict[str, Path]:
    if not source_roots:
        raise LearningLibraryError("at least one source root is required")
    result: dict[str, Path] = {}
    for raw_label, raw_root in source_roots.items():
        label = raw_label.strip().lower()
        if not label or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in label):
            raise LearningLibraryError(f"invalid source root label: {raw_label}")
        root = raw_root.resolve()
        if not root.is_dir():
            raise FileNotFoundError(root)
        if root.is_relative_to(system_root) or system_root.is_relative_to(root):
            raise LearningLibraryError("source and destination roots must not overlap")
        result[label] = root
    return result


def _media_files(root: Path) -> list[Path]:
    return sorted(
        path.resolve() for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES
    )


def _index_formal_media(system_root: Path) -> dict[tuple[int, str], list[Path]]:
    result: dict[tuple[int, str], list[Path]] = {}
    if not system_root.is_dir():
        return result
    for path in _media_files(system_root):
        if path.name.endswith(".partial"):
            continue
        key = (path.stat().st_size, sha256_file(path))
        result.setdefault(key, []).append(path)
    return result


def _read_receipt(path: Path) -> dict[str, object]:
    import json

    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        raise LearningLibraryError("unsupported source migration receipt")
    if payload.get("state") != "verified" or not isinstance(payload.get("items"), list):
        raise LearningLibraryError("source migration receipt is not ready for deletion")
    return payload


def _verify_file(path: Path, item: dict[str, object], role: str) -> None:
    if not path.is_file():
        raise LearningLibraryError(f"{role} file is missing: {path}")
    if path.stat().st_size != int(item.get("bytes") or -1):
        raise LearningLibraryError(f"{role} size changed: {path}")
    if sha256_file(path) != str(item.get("sha256") or ""):
        raise LearningLibraryError(f"{role} checksum changed: {path}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
