"""Rebuild the local materialized industry/concept tag projection through the API."""

from __future__ import annotations

import argparse
import json
from urllib.request import Request, urlopen


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    request = Request(
        args.base_url.rstrip("/") + "/api/instrument-board-tags/rebuild",
        data=b"{}", method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=300) as response:
        print(json.dumps(json.loads(response.read()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
