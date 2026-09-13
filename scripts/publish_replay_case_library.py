from __future__ import annotations

import argparse
from datetime import date, timedelta
from html import escape
import json
from pathlib import Path

from stock_harness.config import load_runtime_settings
from stock_harness.learning_library import CATALOG_SCHEMA_VERSION, write_json
from stock_harness.mean_reversion_replay import SQLiteReplayFutureDataSource
from stock_harness.models import InstrumentKind, StoredDailyBar
from stock_harness.replay import FrozenSignal, select_replay_cases
from stock_harness.sqlite_store import SQLiteMarketDataStore


SYSTEM_ID = "stockharness-replay-cases"


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish balanced replay cases into Learning")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--provider-config", type=Path, default=Path("config/providers.local.yaml"))
    parser.add_argument("--storage-config", type=Path, default=Path("config/storage.local.yaml"))
    parser.add_argument("--library-root", type=Path, default=Path("data/trading-system-learning"))
    parser.add_argument("--per-outcome", type=int, default=8)
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8-sig"))
    cases = select_replay_cases(report.get("evaluations", []), per_outcome=args.per_outcome)
    settings = load_runtime_settings(args.provider_config, args.storage_config)
    with SQLiteMarketDataStore(
        settings.database_path,
        cache_size_kib=settings.sqlite_cache_size_kib,
        mmap_size_mib=settings.sqlite_mmap_size_mib,
        temp_store=settings.sqlite_temp_store,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
    ) as store:
        source = SQLiteReplayFutureDataSource(
            store, date.fromisoformat(str(report["evaluation_through"])),
        )
        for item in cases:
            signal = _signal(item)
            summary = store.get_instrument_summary(signal.symbol) or {}
            item["name"] = str(summary.get("name") or signal.symbol)
            past, basis = _past_bars(store, signal)
            future = list(source.future_bars(signal, 30))
            item["price_basis"] = basis
            item["bars"] = [_bar(value, value.trade_date > signal.signal_date)
                            for value in [*past[-80:], *future]]

    site = args.library_root / "systems" / SYSTEM_ID / "site"
    site.mkdir(parents=True, exist_ok=True)
    write_json(site / "cases.json", {
        "schema_version": "replay-case-publication-v1",
        "source_report": args.report.name,
        "cases": cases,
    })
    (site / "index.html").write_text(_html(cases, args.report.name), encoding="utf-8")
    _update_catalog(args.library_root, args.report.name)
    print(json.dumps({
        "system_id": SYSTEM_ID, "case_count": len(cases),
        "success": sum(item["outcome"] == "success" for item in cases),
        "failure": sum(item["outcome"] == "failure" for item in cases),
        "index": str(site / "index.html"),
    }, ensure_ascii=False))


def _signal(item: dict[str, object]) -> FrozenSignal:
    return FrozenSignal(
        system_id=str(item["system_id"]), system_version=str(item["system_version"]),
        symbol=str(item["symbol"]), scope=str(item["scope"]),
        signal_date=date.fromisoformat(str(item["signal_date"])), direction="long",
        reference_close=float(item["entry_price"]), score=float(item["score"]),
        setup_family=str(item["setup_family"]),
        invalidation_price=float(item["invalidation_price"]),
        selected_target_price=float(item["target_price"]),
        metadata=dict(item.get("metadata") or {}),
    )


def _past_bars(store: SQLiteMarketDataStore, signal: FrozenSignal):
    if store.get_instrument_kind(signal.symbol) is InstrumentKind.STOCK:
        values, basis = store.get_recent_causally_adjusted_stock_bars_many(
            [signal.symbol], signal.signal_date, 100,
        )
        return values.get(signal.symbol, []), basis.get(signal.symbol, "unknown")
    return store.get_daily_bars(
        signal.symbol, signal.signal_date - timedelta(days=240), signal.signal_date,
    ), "canonical-daily"


def _bar(value: StoredDailyBar, future: bool) -> dict[str, object]:
    return {
        "date": value.trade_date.isoformat(), "open": value.open, "high": value.high,
        "low": value.low, "close": value.close, "volume": value.volume, "future": future,
    }


def _update_catalog(root: Path, source_report: str) -> None:
    path = root / "catalog.json"
    catalog = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {
        "schema_version": CATALOG_SCHEMA_VERSION, "systems": [],
    }
    systems = [item for item in catalog.get("systems", [])
               if item.get("system_id") != SYSTEM_ID]
    systems.append({
        "system_id": SYSTEM_ID, "title": "StockHarness回测案例库",
        "methodology": "cross-system-replay-cases", "status": "published",
        "default": False, "corpus_version": source_report,
        "publication_version": "replay-case-site-v1",
        "index_path": f"systems/{SYSTEM_ID}/site/index.html",
    })
    write_json(path, {"schema_version": CATALOG_SCHEMA_VERSION, "systems": systems})


def _html(cases: list[dict[str, object]], report_name: str) -> str:
    cards = "".join(_card(item) for item in cases)
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>StockHarness回测案例库</title><style>
body{{margin:0;background:#101417;color:#d8dee4;font:14px/1.55 Consolas,'Microsoft YaHei UI',sans-serif}}
header{{position:sticky;top:0;background:#161c21;padding:16px 24px;border-bottom:1px solid #303940;z-index:2}}
main{{max-width:1180px;margin:auto;padding:20px}} .case{{border:1px solid #303940;margin:0 0 18px;padding:14px;background:#151b20}}
h1,h2,p{{margin:0 0 8px;font-weight:400}} .success{{color:#ff6b6b}} .failure{{color:#55c98b}}
.meta{{color:#9aa7b2;font-size:12px;display:flex;gap:14px;flex-wrap:wrap}} svg{{width:100%;height:auto;background:#0d1114;margin-top:10px}}
.note{{color:#aeb8c0}} table{{border-collapse:collapse;margin-top:8px}} td{{padding:3px 12px 3px 0}}
</style></head><body><header><h1>StockHarness回测成功与失败案例</h1>
<p class=\"note\">来源：{escape(report_name)}。信号线左侧和当日为当时可见数据；阴影区是事后回放，只用于判定结果，不参与选股。</p></header>
<main>{cards}</main></body></html>"""


def _card(item: dict[str, object]) -> str:
    outcome = str(item["outcome"])
    label = "成功：目标先到" if outcome == "success" else "失败：失效先到"
    returns = "".join(
        f"<td>{escape(str(key))}日 {float(value):+.2%}</td>"
        for key, value in item.get("returns", {}).items()
    )
    return f"""<section class=\"case\"><h2 class=\"{outcome}\">{escape(str(item.get('name') or item['symbol']))} ({escape(str(item['symbol']))}) · {label}</h2>
<div class=\"meta\"><span>信号日 {item['signal_date']}</span><span>{item['scope']} / {item['setup_family']}</span>
<span>第 {item['first_boundary_event_session']} 个交易日触发</span><span>评分 {item['score']}</span><span>{item['price_basis']}</span></div>
<table><tr><td>入场 {item['entry_price']}</td><td>目标 {item['target_price']}</td><td>失效 {item['invalidation_price']}</td>{returns}</tr></table>
{_svg(item)}</section>"""


def _svg(item: dict[str, object]) -> str:
    bars = list(item.get("bars") or [])
    if not bars:
        return "<p>暂无可绘制K线。</p>"
    prices = [float(bar[key]) for bar in bars for key in ("high", "low")]
    prices.extend(float(item[key]) for key in ("entry_price", "target_price", "invalidation_price"))
    low, high = min(prices), max(prices)
    span = max(high - low, 1e-6)
    width, height, pad = 1000, 300, 28
    x_step = (width - pad * 2) / max(len(bars), 1)
    y = lambda value: pad + (high - float(value)) / span * (height - pad * 2)
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="K线案例">']
    first_future = next((index for index, bar in enumerate(bars) if bar["future"]), len(bars))
    split_x = pad + first_future * x_step
    parts.append(f'<rect x="{split_x:.1f}" y="0" width="{width-split_x:.1f}" height="{height}" fill="#ffffff08"/>')
    parts.append(f'<line x1="{split_x:.1f}" x2="{split_x:.1f}" y1="0" y2="{height}" stroke="#d8dee4" stroke-dasharray="4 4"/>')
    for index, bar in enumerate(bars):
        x = pad + (index + .5) * x_step
        color = "#ef6461" if float(bar["close"]) >= float(bar["open"]) else "#45bd7b"
        body_top = y(max(float(bar["open"]), float(bar["close"])))
        body_bottom = y(min(float(bar["open"]), float(bar["close"])))
        parts.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{y(bar["high"]):.1f}" y2="{y(bar["low"]):.1f}" stroke="{color}"/>')
        parts.append(f'<rect x="{x-max(1,x_step*.28):.1f}" y="{body_top:.1f}" width="{max(2,x_step*.56):.1f}" height="{max(1,body_bottom-body_top):.1f}" fill="{color}"/>')
    for key, color, label in (("entry_price", "#62a9ff", "入场"), ("target_price", "#ef6461", "目标"), ("invalidation_price", "#45bd7b", "失效")):
        level = y(item[key])
        parts.append(f'<line x1="0" x2="{width}" y1="{level:.1f}" y2="{level:.1f}" stroke="{color}" stroke-dasharray="6 4" opacity=".8"/>')
        parts.append(f'<text x="6" y="{max(12,level-3):.1f}" fill="{color}" font-size="11">{label} {item[key]}</text>')
    parts.append('</svg>')
    return "".join(parts)


if __name__ == "__main__":
    main()
