#!/usr/bin/env python3
"""Archive all GengGui source media, then optionally delete verified originals."""

from __future__ import annotations

import argparse
from pathlib import Path

from stock_harness.learning_media_migration import (
    archive_source_media,
    delete_verified_sources,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
DEFAULT_SYSTEM_ROOT = (
    PROJECT_ROOT / "data" / "trading-system-learning" / "systems" / "trend-genggui"
)
DEFAULT_RECEIPT = DEFAULT_SYSTEM_ROOT / "source-migration-receipt.json"
DEFAULT_ROOTS = {
    "downloads": Path.home() / "Downloads" / "趋势耿鬼video",
    "tmp-video-study": WORKSPACE_ROOT / "tmp" / "video-study",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("archive", "delete"))
    parser.add_argument("--system-root", type=Path, default=DEFAULT_SYSTEM_ROOT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    args = parser.parse_args()

    if args.action == "archive":
        result = archive_source_media(args.system_root, DEFAULT_ROOTS, args.receipt)
        copied = sum(bool(item["copied"]) for item in result["items"])
        reused = len(result["items"]) - copied
        print(
            f"verified sources={len(result['items'])} copied={copied} reused={reused} "
            f"receipt={args.receipt.resolve()}"
        )
        return

    result = delete_verified_sources(args.system_root, DEFAULT_ROOTS, args.receipt)
    print(
        f"deleted sources={result['deleted_count']} "
        f"receipt={args.receipt.resolve()}"
    )


if __name__ == "__main__":
    main()
