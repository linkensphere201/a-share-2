"""Create an offline blinded chart packet; no returns or duplicate detector."""

import argparse
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from stock_harness.low_base_pullback import ALGORITHM_VERSION, CONFIG, detect_low_base_pullback
from stock_harness.low_base_validation import analysis_bars


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def sample_cases(snapshot, start, end, per_group=6):
    if start > end or per_group < 1:
        raise ValueError("invalid review interval or sample size")
    groups = {name: [] for name in ("eligible", "tracked-not-eligible", "unclassified")}
    for instrument in snapshot["instruments"]:
        rows = instrument["bars"]
        if not rows:
            continue
        if digest(rows) != instrument["bars_sha256"]:
            raise ValueError("snapshot bar checksum mismatch")
        bars = analysis_bars(rows)
        seen = set()
        for index, bar in enumerate(bars):
            day = bar.period_end.isoformat()
            if not start <= day <= end or index < 89:
                continue
            evidence = detect_low_base_pullback(bars[:index + 1])
            group = "eligible" if evidence and evidence["screen_eligible"] else (
                "tracked-not-eligible" if evidence else "unclassified")
            # Deduplicate a lifecycle per group; ordinary controls are monthly.
            key = (group, evidence["launch_date"] if evidence else day[:7])
            if key in seen:
                continue
            seen.add(key)
            identity = f"shape-review-v1:{instrument['symbol']}:{day}"
            groups[group].append({"symbol": instrument["symbol"], "as_of_date": day,
                                  "group": group, "evidence": evidence,
                                  "order": hashlib.sha256(identity.encode()).hexdigest(),
                                  "bars": rows[index - 89:index + 1]})
    selected = []
    for pool in groups.values():
        # At most one chart per symbol in each stratum, avoiding one prolific stock.
        symbols = set()
        for case in sorted(pool, key=lambda c: c["order"]):
            if case["symbol"] in symbols:
                continue
            symbols.add(case["symbol"])
            selected.append(case)
            if len(symbols) == per_group:
                break
    selected.sort(key=lambda c: digest(["blind-order", c["order"]]))
    for index, case in enumerate(selected, 1):
        case["case_id"] = f"S{index:03d}"
    return selected, {key: len(value) for key, value in groups.items()}


def chart(case):
    rows = case["bars"]
    base = rows[0]["close"]
    low = min(b["low"] for b in rows) / base * 100
    high = max(b["high"] for b in rows) / base * 100
    span = max(high - low, 1)
    vmax = max(b["volume"] for b in rows)
    y = lambda price: 25 + (high - price / base * 100) / span * 265
    elements = [f'<svg viewBox="0 0 1000 410" role="img" aria-label="{case["case_id"]} price and volume">']
    for level in range(5):
        price = low + span * level / 4
        height = 25 + (high - price) / span * 265
        elements.append(f'<path d="M55 {height:.2f}H980" stroke="#e5e7eb"/><text x="4" y="{height:.2f}">{price:.1f}</text>')
    for index, row in enumerate(rows):
        x = 65 + index * 10.1
        color = "#c43e4f" if row["close"] >= row["open"] else "#168571"
        top = min(y(row["open"]), y(row["close"]))
        height = max(1, abs(y(row["open"]) - y(row["close"])))
        volume = row["volume"] / vmax * 75
        elements.append(f'<path d="M{x:.2f} {y(row["high"]):.2f}V{y(row["low"]):.2f}" stroke="{color}"/>')
        elements.append(f'<rect x="{x-3:.2f}" y="{top:.2f}" width="6" height="{height:.2f}" fill="{color}"/>')
        elements.append(f'<rect x="{x-3:.2f}" y="{385-volume:.2f}" width="6" height="{volume:.2f}" fill="{color}"/>')
        if index % 15 == 14:
            elements.append(f'<text x="{x-10:.2f}" y="405">{index-89}</text>')
    return "".join(elements) + "</svg>"


def write_packet(snapshot, output, start, end, per_group=6):
    cases, counts = sample_cases(snapshot, start, end, per_group)
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"algorithm_version": ALGORITHM_VERSION, "parameters": asdict(CONFIG),
                "snapshot_sha256": digest(snapshot), "interval": [start, end],
                "source": snapshot.get("source"), "price_basis": snapshot.get("price_basis"),
                "per_group": per_group, "pool_counts": counts, "sample_count": len(cases),
                "scope": "Development-only shape review; not a held-out or population precision estimate.",
                "rubric": {"low_position": "yes/no/uncertain", "volume_pulse": "yes/no/uncertain",
                           "stable_plateau": "yes/no/uncertain", "contracting_retest": "yes/no/uncertain",
                           "overall": "match/partial/nonmatch/uncertain"}}
    for filename, value in (("manifest.json", manifest), ("answer-key.json", cases)):
        with (output / filename).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, indent=2)
    with (output / "labels.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["case_id", *manifest["rubric"], "reviewer", "notes"])
        writer.writeheader()
        writer.writerows({"case_id": case["case_id"]} for case in cases)
    sections = "".join(f'<section><h2>{case["case_id"]}</h2>{chart(case)}</section>' for case in cases)
    html = ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Shape Review</title><style>body{font:14px system-ui;margin:24px;color:#20252b;background:#fff}'
            'main{max-width:1100px;margin:auto}section{border-top:1px solid #aaa;padding:16px 0;break-inside:avoid}'
            'h1{font-size:24px}h2{font-size:18px}svg{width:100%;height:auto}text{font:12px system-ui}</style>'
            '<main><h1>Shape Review</h1>' + sections + '</main></html>')
    (output / "review.html").write_text(html, encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default="2026-08-31")
    parser.add_argument("--per-group", type=int, default=6)
    args = parser.parse_args()
    print(json.dumps(write_packet(json.loads(args.snapshot.read_text(encoding="utf-8")),
                                  args.output, args.start, args.end, args.per_group), indent=2))
