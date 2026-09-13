from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
from pathlib import Path

from stock_harness.config import load_runtime_settings
from stock_harness.review_systems import MEAN_REVERSION_SYSTEM_ID
from stock_harness.sqlite_store import SQLiteMarketDataStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit persisted mean-reversion replay results")
    parser.add_argument("replay_report", type=Path)
    parser.add_argument("--provider-config", type=Path, default=Path("config/providers.local.yaml"))
    parser.add_argument("--storage-config", type=Path, default=Path("config/storage.local.yaml"))
    parser.add_argument(
        "--output", type=Path,
        default=Path(".tmp/reports/mean-reversion-replay-audit.json"),
    )
    args = parser.parse_args()
    replay = json.loads(args.replay_report.read_text(encoding="utf-8"))
    settings = load_runtime_settings(args.provider_config, args.storage_config)
    reports = []
    aggregate_disqualifiers: Counter[str] = Counter()
    eligible_results = []
    with SQLiteMarketDataStore(
        settings.database_path,
        cache_size_kib=settings.sqlite_cache_size_kib,
        mmap_size_mib=settings.sqlite_mmap_size_mib,
        temp_store=settings.sqlite_temp_store,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
    ) as store:
        for replay_result in replay.get("results", []):
            scores = _all_scores(store, str(replay_result["run_id"]))
            scope_counts: Counter[str] = Counter()
            family_counts: Counter[str] = Counter()
            state_counts: Counter[str] = Counter()
            disqualifiers: Counter[str] = Counter()
            none_high_score = 0
            for score in scores:
                scope = str(score.get("entity_scope") or "unknown")
                family = str(score.get("setup_family") or "none")
                state = str(score.get("eligibility", {}).get("state") or "unknown")
                scope_counts[scope] += 1
                family_counts[f"{scope}:{family}"] += 1
                state_counts[f"{scope}:{state}"] += 1
                if family == "none" and float(score.get("total_score") or 0) >= 60:
                    none_high_score += 1
                for reason in score.get("disqualifiers", []):
                    disqualifiers[str(reason)] += 1
                    aggregate_disqualifiers[str(reason)] += 1
                if score.get("eligible"):
                    eligible_results.append({
                        "effective_date": replay_result["effective_date"],
                        "symbol": score.get("symbol"), "name": score.get("name"),
                        "scope": scope, "family": family, "state": state,
                        "score": score.get("total_score"),
                        "stressed_risk_reward": score.get("stressed_risk_reward"),
                        "forward_outcome": _forward_outcome(
                            store, score, date.fromisoformat(replay_result["effective_date"]),
                            date.fromisoformat(replay["through"]),
                        ),
                    })
            reports.append({
                "effective_date": replay_result["effective_date"],
                "run_id": replay_result["run_id"],
                "revision": replay_result.get("revision"),
                "score_count": len(scores),
                "scope_counts": dict(sorted(scope_counts.items())),
                "family_counts": dict(sorted(family_counts.items())),
                "state_counts": dict(sorted(state_counts.items())),
                "eligible_count": sum(scope_counts.values()) and sum(
                    1 for score in scores if score.get("eligible")
                ),
                "none_high_score_count": none_high_score,
                "top_disqualifiers": disqualifiers.most_common(12),
            })
    output = {
        "source_report": str(args.replay_report),
        "session_count": len(reports),
        "sessions": reports,
        "eligible_results": eligible_results,
        "aggregate_disqualifiers": aggregate_disqualifiers.most_common(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps({
        "sessions": len(reports), "eligible": len(eligible_results),
        "output": str(args.output),
    }, ensure_ascii=False))


def _all_scores(store: SQLiteMarketDataStore, run_id: str) -> list[dict[str, object]]:
    result = []
    offset = 0
    while True:
        page = store.list_signal_review_scores(
            run_id, system_id=MEAN_REVERSION_SYSTEM_ID, limit=5000, offset=offset,
        )
        result.extend(page)
        if len(page) < 5000:
            return result
        offset += len(page)


def _forward_outcome(
    store: SQLiteMarketDataStore, score: dict[str, object],
    effective_date: date, available_through: date,
) -> dict[str, object]:
    opportunity = score.get("opportunity")
    if not isinstance(opportunity, dict):
        return {"status": "not-evaluable", "reason": "missing-opportunity"}
    entry = _number(opportunity.get("entry_price"))
    invalidation = _number(opportunity.get("invalidation_price"))
    target = _number(opportunity.get("selected_target_price"))
    if target is None:
        target = _number(score.get("selected_target_price"))
    maximum_holding = int(opportunity.get("maximum_holding_sessions") or 0)
    if entry is None or invalidation is None or target is None or not (
        target > entry > invalidation
    ):
        return {"status": "not-evaluable", "reason": "invalid-price-ordering"}
    bars = store.get_daily_bars(
        str(score["symbol"]), effective_date + timedelta(days=1), available_through,
    )[:maximum_holding]
    if not bars:
        return {"status": "pending", "future_sessions": 0}
    risk = entry - invalidation
    status = "pending"
    event_session = None
    for session, bar in enumerate(bars, 1):
        target_hit = bar.high >= target
        invalidation_hit = bar.low <= invalidation
        if target_hit or invalidation_hit:
            status = (
                "same-session-ambiguous" if target_hit and invalidation_hit
                else "target-first" if target_hit else "invalidation-first"
            )
            event_session = session
            break
    if event_session is None and len(bars) >= maximum_holding:
        status = "expired-neither"
    return {
        "status": status, "event_session": event_session,
        "future_sessions": len(bars),
        "mfe_r": round((max(bar.high for bar in bars) - entry) / risk, 4),
        "mae_r": round((min(bar.low for bar in bars) - entry) / risk, 4),
    }


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


if __name__ == "__main__":
    main()
