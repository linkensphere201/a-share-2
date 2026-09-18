"""Strategy catalog and saved-analysis adapters; no detector implementations."""
from copy import deepcopy
from dataclasses import asdict, dataclass
from stock_harness.analysis_inputs import AnalysisHorizons
from stock_harness.major_descending_lines import MajorLinePeriod, MajorLineState
from stock_harness.first_pullback_pattern import CONFIG as PULLBACK_CONFIG
from stock_harness.low_base_pullback import (
    ALGORITHM_VERSION as LOW_BASE_VERSION, CONFIG as LOW_BASE_CONFIG, LAUNCH_TYPE as LOW_BASE_TYPE,
)
from stock_harness.screener_result_tags import attach_recognition_tags
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
    VolumeAccumulationSignal,
    detect_volume_accumulation, accumulation_rank_key,
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
class ScreenerStrategy:
    definition: dict
    parameter_template: dict
    structure: str | None = None
    kind: str | None = None
    line_code: str | None = None

    @property
    def strategy_id(self):
        return self.definition["strategy_id"]

    @property
    def version(self):
        return self.definition["version"]

    def parameters(self, periods, states, max_results):
        result = deepcopy(self.parameter_template)
        result["max_results"] = max_results
        if self.strategy_id == STRATEGY_ID:
            result.update(periods=[x.value for x in periods], states=[x.value for x in states])
        return result

    def analyze(self, service, request):
        if self.structure == "low-base":
            return service.analyze_low_base_candidate(request)
        if self.structure == "deep-drawdown":
            return service.analyze_deep_drawdown_candidate(request)
        return service.analyze_screening_candidate(request, self.structure)


_SHAPES = {
    PLATFORM_STRATEGY_ID: ("long-platform", PLATFORM_KIND, "LONG-PLATFORM"),
    DEEP_DRAWDOWN_STRATEGY_ID: ("deep-drawdown", DEEP_DRAWDOWN_KIND, "DEEP-DRAWDOWN"),
    BULL_FLAG_STRATEGY_ID: ("bull-flag", BULL_FLAG_KIND, "BULL-FLAG"),
    LOW_BASE_STRATEGY_ID: ("low-base", "first-pullback-range", "LOW-BASE-PULLBACK"),
    PULLBACK_STRATEGY_ID: ("first-pullback", "first-pullback-range", "FIRST-PULLBACK"),
}
STRATEGIES = {
    d["strategy_id"]: ScreenerStrategy(d, _parameter_defaults(d["strategy_id"], (), (), 0),
                                      *_SHAPES.get(d["strategy_id"], (None, None, None)))
    for d in _definitions()
}


def get_strategy(strategy_id: str) -> ScreenerStrategy:
    try:
        return STRATEGIES[strategy_id]
    except KeyError:
        raise ValueError(f"unknown screener strategy: {strategy_id}") from None


def strategy_definitions():
    return [deepcopy(s.definition) for s in STRATEGIES.values()]
