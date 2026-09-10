"""Validated HTTP request payloads for the StockHarness API."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


CustomGroupRole = Literal[
    "", "sentiment_anchor", "liquidity_anchor", "bellwether",
    "core_identity", "lagging_expansion",
]


class CustomGroupMemberInput(BaseModel):
    symbol: str
    role: CustomGroupRole = ""
    tags: list[str] = Field(default_factory=list)
    note: str = ""


class CustomGroupInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    members: list[CustomGroupMemberInput] = Field(default_factory=list, max_length=5000)


class InstrumentTagsInput(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=8)


class CustomIndexMemberInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    weight: float | None = Field(default=None, gt=0)


class CustomIndexInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    base_date: date
    base_value: float = Field(default=1000, gt=0)
    weighting_method: Literal["equal", "manual"] = "equal"
    effective_from: date | None = None
    members: list[CustomIndexMemberInput] = Field(min_length=1, max_length=500)


class IntradaySubscriptionInput(BaseModel):
    group_id: str = Field(min_length=1, max_length=200)
    symbols: list[str] = Field(default_factory=list, max_length=5000)


class IntradayRefreshInput(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=5000)


class FrontendEventInput(BaseModel):
    level: Literal["WARNING", "ERROR"]
    logger: str = Field(default="app", max_length=100)
    message: str = Field(min_length=1, max_length=1000)


class TrendAnalysisInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    timeframes: list[Literal["daily", "weekly", "monthly"]] = Field(
        min_length=1, max_length=3
    )
    short_horizon_bars: int = Field(default=60, ge=1, le=1250)
    medium_horizon_bars: int = Field(default=120, ge=1, le=1250)
    long_horizon_bars: int = Field(default=250, ge=1, le=1250)
    config_version: str = Field(min_length=1, max_length=100)
    include_preview: bool = True


class AiAnalysisReferenceInput(BaseModel):
    code: str = Field(min_length=2, max_length=16, pattern=r"^[A-Z][A-Z0-9_-]*$")
    kind: Literal["level", "line", "pattern"]
    label: str = Field(min_length=1, max_length=100)
    detail: str = Field(default="", max_length=500)
    analysis_item_id: str | None = Field(default=None, max_length=200)
    geometry: dict[str, Any] | None = None


class AiStructureViewInput(BaseModel):
    horizon: Literal["small", "medium"]
    trend: str = Field(min_length=1, max_length=500)
    pattern: str = Field(min_length=1, max_length=500)
    state: str = Field(min_length=1, max_length=200)
    reference_codes: list[str] = Field(min_length=1, max_length=20)


class AiRiskRewardInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    direction: Literal["long", "short"] = "long"
    trigger: str = Field(min_length=1, max_length=500)
    entry_price: float = Field(gt=0)
    stop_price: float = Field(gt=0)
    target_price: float = Field(gt=0)
    reference_codes: list[str] = Field(min_length=1, max_length=20)


class AiAnalysisFrameworkInput(BaseModel):
    key_level_codes: list[str] = Field(min_length=1, max_length=50)
    structures: list[AiStructureViewInput] = Field(min_length=2, max_length=2)
    risk_reward: list[AiRiskRewardInput] = Field(min_length=1, max_length=20)


class AiAnalysisReportInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=200)
    timeframe: Literal["daily", "weekly", "monthly"] = "daily"
    as_of_date: date
    source_run_id: str | None = Field(default=None, max_length=64)
    title: str = Field(min_length=1, max_length=160)
    conclusion_markdown: str = Field(min_length=1, max_length=20_000)
    framework: AiAnalysisFrameworkInput
    references: list[AiAnalysisReferenceInput] = Field(min_length=1, max_length=100)
    author: str = Field(default="codex", min_length=1, max_length=80)


class AiChatConversationInput(BaseModel):
    context_kind: Literal["trend_analysis", "signal_run", "signal_workspace"] = "trend_analysis"
    context_id: str | None = Field(default=None, min_length=1, max_length=64)
    symbol: str | None = Field(default=None, min_length=1, max_length=200)
    timeframe: Literal["daily"] = "daily"
    source_run_id: str | None = Field(default=None, min_length=1, max_length=64)
    force_new: bool = False

    @model_validator(mode="after")
    def validate_context(self) -> "AiChatConversationInput":
        context_id = self.context_id or self.source_run_id
        if not context_id:
            raise ValueError("chat context_id or source_run_id is required")
        if self.context_kind == "trend_analysis" and not self.symbol:
            raise ValueError("trend chat requires symbol")
        return self


class AiChatConversationUpdateInput(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    status: Literal["active", "archived"] | None = None


class AiPositionContextInput(BaseModel):
    direction: Literal["long", "short"] = "long"
    entry_price: float = Field(gt=0)
    entry_date: date | None = None
    quantity: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)
    note: str = Field(default="", max_length=500)


class AiRiskRewardContextInput(BaseModel):
    direction: Literal["long", "short"] = "long"
    entry_price: float = Field(gt=0)
    stop_price: float = Field(gt=0)
    target_price: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_price_order(self) -> "AiRiskRewardContextInput":
        valid = (
            self.stop_price < self.entry_price < self.target_price
            if self.direction == "long"
            else self.target_price < self.entry_price < self.stop_price
        )
        if not valid:
            raise ValueError("risk/reward prices do not match the selected direction")
        return self


class AiChatTurnInput(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)
    template_id: str | None = Field(default=None, max_length=80)
    position: AiPositionContextInput | None = None
    risk_reward: AiRiskRewardContextInput | None = None
    selected_signal_item_ids: list[str] = Field(default_factory=list, max_length=20)
    selected_signal_run_id: str | None = Field(default=None, min_length=1, max_length=64)


class TrendReviewSourceInput(BaseModel):
    provider: str = Field(min_length=1, max_length=100)
    dataset: str = Field(min_length=1, max_length=200)
    checked_on: date


class TrendReviewCreateInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    timeframe: Literal["daily"] = "daily"
    horizon: Literal["short", "long"] = "short"
    interval_start: date
    interval_end: date
    as_of_date: date
    dataset_version: str = Field(min_length=1, max_length=100)
    classification: Literal["positive", "near-miss", "ambiguous", "robustness"] = "ambiguous"
    tags: list[str] = Field(min_length=1, max_length=20)
    rationale: str = Field(default="", max_length=2000)
    sources: list[TrendReviewSourceInput] = Field(min_length=1, max_length=20)
    short_horizon_bars: int = Field(default=60, ge=20, le=120)
    medium_horizon_bars: int = Field(default=120, ge=60, le=500)
    long_horizon_bars: int = Field(default=250, ge=120, le=1250)
    config_version: str = Field(default="trend-review-ui-v1", min_length=1, max_length=100)


class TrendReviewLabelInput(BaseModel):
    item_id: str = Field(min_length=1, max_length=200)
    item_type: str = Field(min_length=1, max_length=50)
    decision: Literal["pending", "accepted", "rejected", "ambiguous"]
    payload: dict[str, Any]
    rationale: str = Field(default="", max_length=1000)


class TrendReviewUpdateInput(BaseModel):
    revision: int = Field(ge=1)
    review_status: Literal["proposed", "ambiguous", "confirmed", "rejected"]
    labels: list[TrendReviewLabelInput] = Field(max_length=5000)
    expected: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(default="", max_length=2000)


class ScreenerRunInput(BaseModel):
    strategy_id: Literal["major-descending-breakout"] = "major-descending-breakout"
    periods: list[Literal["6m", "1y"]] = Field(
        default_factory=lambda: ["6m", "1y"], min_length=1,
    )
    states: list[Literal["critical-breakout", "breakout-retest", "broken-out"]] = Field(
        default_factory=lambda: ["critical-breakout", "breakout-retest", "broken-out"],
        min_length=1,
    )
    max_results: int = Field(default=200, ge=1, le=500)
    as_of_date: date | None = None


class SignalReviewRunInput(BaseModel):
    effective_date: date | None = None


class SignalAttentionInput(BaseModel):
    manual_pinned: bool
    effective_date: date | None = None
