"""Strategy catalog and saved-analysis adapters; no detector implementations."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import date
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Sequence

if TYPE_CHECKING:
    from stock_harness.pattern_analysis import PatternAnalysisRequest, PatternAnalysisResult, PatternAnalysisService

from stock_harness.analysis_inputs import AnalysisHorizons
from stock_harness.major_descending_lines import MajorLinePeriod, MajorLineState
from stock_harness.first_pullback_pattern import CONFIG as PULLBACK_CONFIG
from stock_harness.low_base_pullback import (
    ALGORITHM_VERSION as LOW_BASE_VERSION, CONFIG as LOW_BASE_CONFIG, LAUNCH_TYPE as LOW_BASE_TYPE,
)
from stock_harness.long_platform_pattern import (
    STRATEGY_ID as PLATFORM_STRATEGY_ID, ALGORITHM_VERSION as PLATFORM_VERSION,
    KIND as PLATFORM_KIND, CONFIG as PLATFORM_CONFIG,
)
from stock_harness.deep_drawdown_pattern import (
    STRATEGY_ID as DEEP_DRAWDOWN_STRATEGY_ID, ALGORITHM_VERSION as DEEP_DRAWDOWN_VERSION,
    KIND as DEEP_DRAWDOWN_KIND, STATE as DEEP_DRAWDOWN_STATE, MIN_SCORE,
    REFERENCE_SYMBOL, REFERENCE_START, REFERENCE_END,
)
from stock_harness.bull_flag_pattern import (
    STRATEGY_ID as BULL_FLAG_STRATEGY_ID, ALGORITHM_VERSION as BULL_FLAG_VERSION,
    CONFIG as BULL_FLAG_CONFIG, KIND as BULL_FLAG_KIND,
)
from stock_harness.volume_accumulation import (
    STATE as ACCUMULATION_STATE,
    STRATEGY_ID as ACCUMULATION_STRATEGY_ID,
    STRATEGY_VERSION as ACCUMULATION_STRATEGY_VERSION,
)

STRATEGY_ID = "major-descending-breakout"
STRATEGY_VERSION = "major-descending-breakout-v5"
CONFIG_VERSION = "screener-major-descending-v5"
PULLBACK_STRATEGY_ID = "strong-first-pullback"
PULLBACK_STRATEGY_VERSION = "strong-first-pullback-v3"
LOW_BASE_STRATEGY_ID = "low-base-platform-pullback"
DEFAULT_HORIZONS = AnalysisHorizons(60, 120, 250)
SCREENABLE_STATES = (
    MajorLineState.CRITICAL_BREAKOUT,
    MajorLineState.BREAKOUT_RETEST,
    MajorLineState.BROKEN_OUT,
)

def _definitions() -> list[dict[str, object]]:
    return [{
        "strategy_id": PLATFORM_STRATEGY_ID, "name": "长期横盘平台", "version": PLATFORM_VERSION,
        "window": 250, "states": ["shape-match"], "final_bars_only": True,
    }, {
        "strategy_id": STRATEGY_ID,
        "name": "大斜边突破",
        "version": STRATEGY_VERSION,
        "periods": [MajorLinePeriod.HALF_YEAR.value, MajorLinePeriod.YEAR.value],
        "states": [item.value for item in SCREENABLE_STATES],
        "final_bars_only": True,
    }, {
        "strategy_id": ACCUMULATION_STRATEGY_ID,
        "name": "20日堆量蓄势",
        "version": ACCUMULATION_STRATEGY_VERSION,
        "window": 20,
        "context_window": 80,
        "states": [ACCUMULATION_STATE],
        "final_bars_only": True,
    }, {
        "strategy_id": LOW_BASE_STRATEGY_ID,
        "name": "低位平台回踩",
        "version": LOW_BASE_VERSION,
        "window": LOW_BASE_CONFIG.observation_window_sessions,
        "states": ["pullback-observation", "pullback-confirmed"],
        "final_bars_only": True,
    }, {
        "strategy_id": BULL_FLAG_STRATEGY_ID, "name": "牛旗盘整",
        "version": BULL_FLAG_VERSION, "window": BULL_FLAG_CONFIG.max_launch_age,
        "states": ["pullback-observation"], "final_bars_only": True,
    }, {
        "strategy_id": PULLBACK_STRATEGY_ID,
        "name": "强势股首次回踩",
        "version": PULLBACK_STRATEGY_VERSION,
        "window": PULLBACK_CONFIG.observation_window_sessions,
        "states": ["pullback-observation", "pullback-confirmed"],
        "final_bars_only": True,
    }, {
        "strategy_id": DEEP_DRAWDOWN_STRATEGY_ID, "name": "深跌缩量整理",
        "version": DEEP_DRAWDOWN_VERSION, "window": 60,
        "states": [DEEP_DRAWDOWN_STATE], "final_bars_only": True,
    }]


def _parameter_defaults(strategy_id, periods, states, max_results: int) -> dict[str, object]:
    if strategy_id == PLATFORM_STRATEGY_ID:
        return {"max_results": max_results, "final_bars_only": True, "states": ["shape-match"],
                "pattern_parameters": asdict(PLATFORM_CONFIG), "analysis_config": PLATFORM_VERSION}
    if strategy_id == DEEP_DRAWDOWN_STRATEGY_ID:
        return {"max_results": max_results, "final_bars_only": True, "window": 60,
                "states": [DEEP_DRAWDOWN_STATE], "minimum_score": MIN_SCORE,
                "reference_symbol": REFERENCE_SYMBOL, "reference_start": REFERENCE_START,
                "reference_end": REFERENCE_END, "analysis_config": DEEP_DRAWDOWN_VERSION}
    if strategy_id == BULL_FLAG_STRATEGY_ID:
        return {"max_results": max_results, "final_bars_only": True,
                "window": BULL_FLAG_CONFIG.max_launch_age, "states": ["pullback-observation"],
                "pattern_parameters": asdict(BULL_FLAG_CONFIG), "analysis_config": BULL_FLAG_VERSION}
    if strategy_id == LOW_BASE_STRATEGY_ID:
        return {"max_results": max_results, "final_bars_only": True,
                "window": LOW_BASE_CONFIG.observation_window_sessions,
                "states": ["pullback-observation", "pullback-confirmed"],
                "pattern_parameters": asdict(LOW_BASE_CONFIG), "analysis_config": LOW_BASE_VERSION}
    if strategy_id == PULLBACK_STRATEGY_ID:
        return {"max_results": max_results, "final_bars_only": True,
                "window": PULLBACK_CONFIG.observation_window_sessions,
                "states": ["pullback-observation", "pullback-confirmed"],
                "analysis_config": PULLBACK_STRATEGY_VERSION}
    if strategy_id == ACCUMULATION_STRATEGY_ID:
        return {
            "window": 20, "context_window": 80,
            "max_results": max_results, "final_bars_only": True,
        }
    return {
        "periods": [item.value for item in periods],
        "states": [item.value for item in states],
        "max_results": max_results, "final_bars_only": True,
    }



@dataclass(frozen=True)
class ShapeSelection:
    structure: str
    kind: str
    line_code: str
    low_base: bool = False

    def matches(self, item: dict[str, Any], cutoff: date) -> bool:
        evidence = item['payload']
        return (item['item_type'] == 'zone' and evidence.get('kind') == self.kind
                and ((evidence.get('launch_type') == LOW_BASE_TYPE) == self.low_base)
                and bool(evidence.get('screen_eligible'))
                and evidence.get('stage') in {'pullback-observation', 'pullback-confirmed', DEEP_DRAWDOWN_STATE}
                and evidence.get('as_of_date') == cutoff.isoformat())

    def rank_key(self, candidate: dict[str, Any]) -> tuple[float, bool, float, str]:
        return (candidate['evidence'].get('maturity_rank', 3) if self.low_base else 0,
                not self.low_base and candidate['state'] != 'pullback-confirmed',
                -candidate['score'], candidate['symbol'])


@dataclass(frozen=True)
class ScreenerStrategy:
    _definition: dict[str, object]
    _parameter_template: dict[str, object]
    execution: Literal['shape', 'major', 'accumulation']
    analysis_config: str
    selection: ShapeSelection | None = None
    valid_from: date | None = None
    excluded_symbols: frozenset[str] = frozenset()

    @property
    def definition(self) -> dict[str, object]:
        return deepcopy(self._definition)

    @property
    def structure(self) -> str | None:
        return self.selection.structure if self.selection else None

    @property
    def kind(self) -> str | None:
        return self.selection.kind if self.selection else None

    @property
    def line_code(self) -> str | None:
        return self.selection.line_code if self.selection else None

    @property
    def strategy_id(self) -> str:
        return str(self._definition["strategy_id"])

    @property
    def version(self) -> str:
        return str(self._definition["version"])

    def parameters(self, periods: Sequence[MajorLinePeriod], states: Sequence[MajorLineState],
                   max_results: int) -> dict[str, object]:
        result = deepcopy(self._parameter_template)
        result["max_results"] = max_results
        if self.execution == 'major':
            result.update(periods=[x.value for x in periods], states=[x.value for x in states])
        return result

    def validate_cutoff(self, cutoff: date) -> None:
        if self.valid_from is not None and cutoff < self.valid_from:
            raise ValueError(f"{self.strategy_id} reference is only available from {self.valid_from}")

    def allows_symbol(self, symbol: str) -> bool:
        return symbol not in self.excluded_symbols

    def analyze(self, service: PatternAnalysisService, request: PatternAnalysisRequest) -> PatternAnalysisResult | None:
        if self.selection is None:
            raise ValueError(f"{self.strategy_id} requires its {self.execution} executor")
        if self.structure == "low-base":
            return service.analyze_low_base_candidate(request)
        if self.structure == "deep-drawdown":
            return service.analyze_deep_drawdown_candidate(request)
        return service.analyze_screening_candidate(request, self.structure)


_SHAPES = {
    PLATFORM_STRATEGY_ID: ShapeSelection("long-platform", PLATFORM_KIND, "LONG-PLATFORM"),
    DEEP_DRAWDOWN_STRATEGY_ID: ShapeSelection("deep-drawdown", DEEP_DRAWDOWN_KIND, "DEEP-DRAWDOWN"),
    BULL_FLAG_STRATEGY_ID: ShapeSelection("bull-flag", BULL_FLAG_KIND, "BULL-FLAG"),
    LOW_BASE_STRATEGY_ID: ShapeSelection("low-base", "first-pullback-range", "LOW-BASE-PULLBACK", low_base=True),
    PULLBACK_STRATEGY_ID: ShapeSelection("first-pullback", "first-pullback-range", "FIRST-PULLBACK"),
}
STRATEGIES = MappingProxyType({
    d["strategy_id"]: ScreenerStrategy(
        d, _parameter_defaults(d["strategy_id"], (), (), 0),
        execution='shape' if d['strategy_id'] in _SHAPES else 'major' if d['strategy_id'] == STRATEGY_ID else 'accumulation',
        analysis_config=str(d['version']) if d['strategy_id'] in _SHAPES else CONFIG_VERSION,
        selection=_SHAPES.get(d['strategy_id']),
        valid_from=date.fromisoformat(REFERENCE_END) if d['strategy_id'] == DEEP_DRAWDOWN_STRATEGY_ID else None,
        excluded_symbols=frozenset({REFERENCE_SYMBOL}) if d['strategy_id'] == DEEP_DRAWDOWN_STRATEGY_ID else frozenset(),
    )
    for d in _definitions()
})


def get_strategy(strategy_id: str) -> ScreenerStrategy:
    try:
        return STRATEGIES[strategy_id]
    except KeyError:
        raise ValueError(f"unknown screener strategy: {strategy_id}") from None


def strategy_definitions() -> list[dict[str, object]]:
    return [s.definition for s in STRATEGIES.values()]
