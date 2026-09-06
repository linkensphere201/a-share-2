#!/usr/bin/env python3
"""Copy the existing GengGui course media into the formal learning library."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlparse

from stock_harness.learning_library import (
    CATALOG_SCHEMA_VERSION, MANIFEST_SCHEMA_VERSION,
    copy_verified_media, sha256_file, write_json,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
DEFAULT_LIBRARY_ROOT = PROJECT_ROOT / "data" / "trading-system-learning"
DEFAULT_MEDIA_MAP = (
    WORKSPACE_ROOT / "2026-07-27-stock-harness" / "context" / "video-study"
    / "html" / "assets" / "media-map.local.js"
)
DEFAULT_DOWNLOADS = Path.home() / "Downloads" / "趋势耿鬼video"
SYSTEM_ID = "trend-genggui"


def load_media_map(path: Path) -> dict[str, Path]:
    text = path.read_text(encoding="utf-8-sig")
    match = re.fullmatch(r"window\.VIDEO_STUDY_MEDIA = (\{.*\});\s*", text, re.DOTALL)
    if not match:
        raise ValueError(f"invalid local media map: {path}")
    payload = json.loads(match.group(1))
    result: dict[str, Path] = {}
    for episode_id, uri in payload.items():
        parsed = urlparse(str(uri))
        if parsed.scheme != "file":
            raise ValueError(f"unsupported media URI for {episode_id}")
        local_path = unquote(parsed.path.lstrip("/"))
        result[str(episode_id)] = Path(local_path)
    return result


def migrate(library_root: Path, media_map: Path, downloads: Path) -> dict[str, object]:
    system_root = library_root / "systems" / SYSTEM_ID
    media_root = system_root / "media"
    inbox_root = system_root / "inbox"
    mapped = load_media_map(media_map)
    episodes = []
    source_paths: set[Path] = set()
    for episode_id, source in sorted(mapped.items()):
        source = source.resolve()
        source_paths.add(source)
        destination = media_root / f"{episode_id}{source.suffix.lower()}"
        result = copy_verified_media(source, destination)
        episodes.append({
            "episode_id": episode_id, "status": "published",
            "file": destination.relative_to(system_root).as_posix(),
            "source_name": source.name, "bytes": result["bytes"],
            "sha256": result["sha256"],
        })
        print(
            f"episode={episode_id} copied={result['copied']} "
            f"bytes={result['bytes']} file={destination}"
        )

    inbox = []
    if downloads.is_dir():
        for source in sorted(downloads.glob("*.mp4")):
            source = source.resolve()
            if source in source_paths:
                continue
            digest = sha256_file(source)
            destination = inbox_root / f"pending-{digest[:12]}{source.suffix.lower()}"
            result = copy_verified_media(source, destination)
            inbox.append({
                "status": "pending", "file": destination.relative_to(system_root).as_posix(),
                "source_name": source.name, "bytes": result["bytes"],
                "sha256": result["sha256"],
            })
            print(
                f"inbox={destination.name} copied={result['copied']} "
                f"bytes={result['bytes']}"
            )

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "system_id": SYSTEM_ID,
        "title": "趋势耿鬼趋势交易体系",
        "methodology": "trend-trading",
        "episodes": episodes,
        "inbox": inbox,
    }
    write_json(system_root / "media-manifest.json", manifest)
    write_json(library_root / "catalog.json", {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "systems": [{
            "system_id": SYSTEM_ID, "title": manifest["title"],
            "methodology": manifest["methodology"], "status": "published",
            "default": True, "corpus_version": "genggui-28-v1",
            "publication_version": "video-study-html-v1",
            "index_path": f"systems/{SYSTEM_ID}/site/index.html",
        }],
    })
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-root", type=Path, default=DEFAULT_LIBRARY_ROOT)
    parser.add_argument("--media-map", type=Path, default=DEFAULT_MEDIA_MAP)
    parser.add_argument("--downloads", type=Path, default=DEFAULT_DOWNLOADS)
    args = parser.parse_args()
    manifest = migrate(
        args.library_root.resolve(), args.media_map.resolve(), args.downloads.resolve()
    )
    print(
        f"complete episodes={len(manifest['episodes'])} inbox={len(manifest['inbox'])} "
        f"root={args.library_root.resolve()}"
    )


if __name__ == "__main__":
    main()
