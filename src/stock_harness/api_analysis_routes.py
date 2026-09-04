"""Screener, generated-analysis, AI-analysis, and trend-review routes."""

from __future__ import annotations

import logging
import time
from typing import Literal, cast

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from stock_harness.analysis_inputs import AnalysisHorizons, AnalysisTimeframe
from stock_harness.analysis_results import AnalysisNamespace
from stock_harness.api_models import (
    AiAnalysisReportInput,
    ScreenerRunInput,
    TrendAnalysisInput,
    TrendReviewCreateInput,
    TrendReviewUpdateInput,
)
from stock_harness.api_support import (
    json_analysis_run,
    json_trend_review,
    normalize_instrument_symbol,
    store,
    validate_ai_analysis_payload,
)
from stock_harness.major_descending_lines import MajorLinePeriod, MajorLineState
from stock_harness.screener import ScreenerBusyError
from stock_harness.trend_analysis import TrendAnalysisService
from stock_harness.trend_review_set import has_scoreable_expected_labels
from stock_harness.trend_reviews import (
    TrendReviewDecision,
    TrendReviewDraftSpec,
    TrendReviewLabel,
    TrendReviewStatus,
    labels_from_analysis,
)


LOGGER = logging.getLogger(__name__)


def create_analysis_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/screener/strategies")
    def screener_strategies(request: Request) -> dict[str, object]:
        return {"items": request.app.state.screener.strategies()}

    @router.post("/api/screener/runs", status_code=status.HTTP_202_ACCEPTED)
    def start_screener_run(
        payload: ScreenerRunInput, request: Request
    ) -> dict[str, object]:
        try:
            return request.app.state.screener.start_run(
                [MajorLinePeriod(item) for item in payload.periods],
                [MajorLineState(item) for item in payload.states],
                payload.max_results,
                payload.as_of_date,
            )
        except ScreenerBusyError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/api/screener/runs")
    def list_screener_runs(
        request: Request, limit: int = Query(default=10, ge=1, le=10)
    ) -> dict[str, object]:
        return {"items": store(request).list_screener_runs(limit)}

    @router.get("/api/screener/runs/{run_id}")
    def get_screener_run(run_id: str, request: Request) -> dict[str, object]:
        result = store(request).get_screener_run(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="screener run not found")
        return result

    @router.delete(
        "/api/screener/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT
    )
    def delete_screener_run(run_id: str, request: Request) -> Response:
        try:
            deleted = store(request).delete_screener_run(run_id)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if not deleted:
            raise HTTPException(status_code=404, detail="screener run not found")
        LOGGER.info("screener_run_deleted run_id=%s", run_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/api/screener/runs/{run_id}/candidates")
    def list_screener_candidates(run_id: str, request: Request) -> dict[str, object]:
        selected_store = store(request)
        if selected_store.get_screener_run(run_id) is None:
            raise HTTPException(status_code=404, detail="screener run not found")
        return {"items": selected_store.list_screener_candidates(run_id)}

    @router.get("/api/analysis/runs/{run_id}")
    def generated_analysis_run(run_id: str, request: Request) -> dict[str, object]:
        result = store(request).get_generated_analysis_run(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="analysis run not found")
        return cast(dict[str, object], json_analysis_run(result))

    @router.get("/api/analysis/trend/{symbol}/runs")
    def trend_analysis_runs(
        symbol: str, request: Request,
        timeframe: Literal["daily", "weekly", "monthly"] = "daily",
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, object]:
        return {"items": store(request).list_generated_analysis_runs(
            symbol, "trend", timeframe, limit
        )}

    @router.post("/api/analysis/trend/recalculate")
    def recalculate_trend_analysis(
        payload: TrendAnalysisInput, request: Request
    ) -> dict[str, object]:
        horizons = AnalysisHorizons(
            payload.short_horizon_bars,
            payload.medium_horizon_bars,
            payload.long_horizon_bars,
        )
        try:
            results = TrendAnalysisService(store(request)).recalculate(
                payload.symbol,
                [AnalysisTimeframe(item) for item in payload.timeframes],
                horizons,
                config_version=payload.config_version,
                include_preview=payload.include_preview,
            )
        except ValueError as error:
            LOGGER.warning(
                "trend_analysis_rejected symbol=%s error=%s", payload.symbol, error
            )
            raise HTTPException(status_code=422, detail=str(error)) from error
        except RuntimeError as error:
            LOGGER.warning(
                "trend_analysis_busy symbol=%s error=%s", payload.symbol, error
            )
            raise HTTPException(status_code=409, detail=str(error)) from error
        LOGGER.info(
            "trend_analysis_completed symbol=%s timeframes=%s runs=%s",
            payload.symbol, payload.timeframes, len(results),
        )
        return {"status": "completed", "results": results}

    @router.get("/api/analysis/trend/{symbol}")
    def latest_trend_analysis(
        symbol: str,
        request: Request,
        timeframe: Literal["daily", "weekly", "monthly"] = "daily",
    ) -> dict[str, object]:
        selected_store = store(request)
        official = selected_store.get_latest_generated_analysis_run(
            symbol, "trend", timeframe, AnalysisNamespace.OFFICIAL
        )
        preview = selected_store.get_latest_generated_analysis_run(
            symbol, "trend", timeframe, AnalysisNamespace.PREVIEW
        )
        now_ms = int(time.time() * 1000)
        preview_expired = bool(
            preview is not None
            and preview.get("expires_at_ms") is not None
            and int(preview["expires_at_ms"]) <= now_ms
        )
        effective = official
        if preview is not None and not preview_expired and (
            official is None or preview["as_of_date"] >= official["as_of_date"]
        ):
            effective = preview
        return {
            "symbol": symbol.upper(), "timeframe": timeframe,
            "official": json_analysis_run(official),
            "preview": json_analysis_run(preview),
            "preview_expired": preview_expired,
            "effective": json_analysis_run(effective),
        }

    @router.get("/api/analysis/ai/{symbol}")
    def latest_ai_analysis(
        symbol: str,
        request: Request,
        timeframe: Literal["daily", "weekly", "monthly"] = "daily",
    ) -> dict[str, object]:
        report = store(request).get_latest_ai_analysis_report(
            normalize_instrument_symbol(symbol), timeframe
        )
        if report is None:
            raise HTTPException(status_code=404, detail="AI analysis report not found")
        return report

    @router.get("/api/analysis/ai/{symbol}/reports")
    def ai_analysis_reports(
        symbol: str, request: Request,
        timeframe: Literal["daily", "weekly", "monthly"] = "daily",
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, object]:
        return {"items": store(request).list_ai_analysis_reports(
            normalize_instrument_symbol(symbol), timeframe, limit
        )}

    @router.post("/api/analysis/ai", status_code=status.HTTP_201_CREATED)
    def create_ai_analysis(
        payload: AiAnalysisReportInput, request: Request
    ) -> dict[str, object]:
        selected_store = store(request)
        try:
            framework, references = validate_ai_analysis_payload(selected_store, payload)
            report = selected_store.create_ai_analysis_report(
                symbol=payload.symbol,
                timeframe=payload.timeframe,
                as_of_date=payload.as_of_date,
                source_run_id=payload.source_run_id,
                title=payload.title,
                conclusion_markdown=payload.conclusion_markdown,
                framework=framework,
                references=references,
                author=payload.author,
            )
        except ValueError as error:
            LOGGER.warning("ai_analysis_rejected symbol=%s error=%s", payload.symbol, error)
            raise HTTPException(status_code=422, detail=str(error)) from error
        LOGGER.info(
            "ai_analysis_created symbol=%s timeframe=%s revision=%s report_id=%s",
            report["symbol"], report["timeframe"], report["revision"], report["report_id"],
        )
        return report

    @router.post("/api/trend-reviews", status_code=status.HTTP_201_CREATED)
    def create_trend_review(
        payload: TrendReviewCreateInput, request: Request
    ) -> dict[str, object]:
        horizons = AnalysisHorizons(
            payload.short_horizon_bars,
            payload.medium_horizon_bars,
            payload.long_horizon_bars,
        )
        try:
            snapshot = TrendAnalysisService(store(request)).build_review_snapshot(
                payload.symbol,
                AnalysisTimeframe(payload.timeframe),
                horizons,
                as_of_date=payload.as_of_date,
                config_version=payload.config_version,
            )
            labels = labels_from_analysis(snapshot["items"], payload.horizon)
            review = store(request).create_trend_review(TrendReviewDraftSpec(
                symbol=payload.symbol,
                timeframe=payload.timeframe,
                horizon=payload.horizon,
                interval_start=payload.interval_start,
                interval_end=payload.interval_end,
                as_of_date=payload.as_of_date,
                dataset_version=payload.dataset_version,
                classification=payload.classification,
                status=TrendReviewStatus.PROPOSED,
                tags=payload.tags,
                labels=labels,
                expected={},
                rationale=payload.rationale,
                sources=[item.model_dump(mode="json") for item in payload.sources],
                input_digest=bytes.fromhex(str(snapshot["input_digest"])),
                algorithm_version=str(snapshot["algorithm_version"]),
                config_version=str(snapshot["config_version"]),
                settings={
                    "short_horizon_bars": payload.short_horizon_bars,
                    "medium_horizon_bars": payload.medium_horizon_bars,
                    "long_horizon_bars": payload.long_horizon_bars,
                },
            ))
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        LOGGER.info(
            "trend_review_created review_id=%s symbol=%s as_of=%s labels=%s",
            review["review_id"], payload.symbol, payload.as_of_date, len(labels),
        )
        return {"review": json_trend_review(review), "analysis": snapshot}

    @router.get("/api/trend-reviews")
    def list_trend_reviews(
        request: Request,
        symbol: str | None = None,
        review_status: Literal["proposed", "ambiguous", "confirmed", "rejected"] | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, object]:
        status_filter = TrendReviewStatus(review_status) if review_status else None
        items = store(request).list_trend_reviews(
            symbol=symbol, status=status_filter, limit=limit
        )
        return {"items": [json_trend_review(item) for item in items]}

    @router.get("/api/trend-reviews/{review_id}")
    def get_trend_review(review_id: str, request: Request) -> dict[str, object]:
        review = store(request).get_trend_review(review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="trend review not found")
        return json_trend_review(review)

    @router.post("/api/trend-reviews/{review_id}/snapshot")
    def rebuild_trend_review_snapshot(
        review_id: str, request: Request
    ) -> dict[str, object]:
        review = store(request).get_trend_review(review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="trend review not found")
        settings = review["settings"]
        if not isinstance(settings, dict):
            raise HTTPException(status_code=409, detail="trend review settings are invalid")
        try:
            snapshot = TrendAnalysisService(store(request)).build_review_snapshot(
                str(review["symbol"]),
                AnalysisTimeframe(str(review["timeframe"])),
                AnalysisHorizons(
                    int(settings["short_horizon_bars"]),
                    int(settings["medium_horizon_bars"]),
                    int(settings["long_horizon_bars"]),
                ),
                as_of_date=review["as_of_date"],
                config_version=str(review["config_version"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if str(snapshot["input_digest"]) != bytes(review["input_digest"]).hex():
            raise HTTPException(
                status_code=409,
                detail="trend review input has changed since the draft was created",
            )
        return snapshot

    @router.put("/api/trend-reviews/{review_id}")
    def update_trend_review(
        review_id: str,
        payload: TrendReviewUpdateInput,
        request: Request,
    ) -> dict[str, object]:
        if (
            payload.review_status == TrendReviewStatus.CONFIRMED.value
            and not has_scoreable_expected_labels(payload.expected)
        ):
            raise HTTPException(
                status_code=422,
                detail="confirmed trend review requires scoreable expected labels",
            )
        if (
            payload.review_status == TrendReviewStatus.CONFIRMED.value
            and any(
                item.decision == TrendReviewDecision.PENDING.value
                for item in payload.labels
            )
        ):
            raise HTTPException(
                status_code=422,
                detail="confirmed trend review cannot contain pending labels",
            )
        labels = [TrendReviewLabel(
            item_id=item.item_id,
            item_type=item.item_type,
            decision=TrendReviewDecision(item.decision),
            payload=item.payload,
            rationale=item.rationale,
        ) for item in payload.labels]
        try:
            review = store(request).update_trend_review(
                review_id,
                expected_revision=payload.revision,
                status=TrendReviewStatus(payload.review_status),
                labels=labels,
                expected=payload.expected,
                rationale=payload.rationale,
            )
        except ValueError as error:
            status_code = 404 if str(error) == "trend review not found" else 422
            raise HTTPException(status_code=status_code, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        LOGGER.info(
            "trend_review_updated review_id=%s status=%s revision=%s",
            review_id, payload.review_status, review["revision"],
        )
        return json_trend_review(review)

    return router
