from copy import deepcopy
from datetime import date, timedelta

import pytest

from stock_harness.hotspot_session_engine import replay_hotspot_sessions, SessionHotspotScorer
from stock_harness.board_hotspot_unified import UnifiedHotspotScorer


def feature(index, *, strong=(), impulse=(), healthy=(), ret1=0, rs1=0, ret5=.03, breadth=.7):
    return {"effective_date": (date(2026, 9, 1) + timedelta(days=index)).isoformat(),
        "coverage_state": "complete", "metrics": {"returns": {"1": ret1, "5": ret5},
            "relative_strength": {"1": rs1, "5": .03}, "volume_ratio20": 1.5,
            "recent_volume_ratio_5_5": 1.1},
        "member_snapshot": {"member_count": 10, "return_5_covered_count": 10, "coverage_ratio": 1,
            "positive_return_1_ratio": breadth, "positive_return_5_ratio": .7,
            "strong_member_symbols": list(strong), "impulse_leader_symbols": list(impulse),
            "healthy_member_symbols": list(healthy), "covered_member_symbols": [str(i) for i in range(10)]}}


def test_pulse_is_maintenance_not_repeated_expansion():
    features = [feature(i, strong=["1", "2", "3"], ret1=.03 if i == 0 else 0,
                        rs1=.03 if i == 0 else 0) for i in range(5)]
    old = UnifiedHotspotScorer().score({"symbol": "B", "session_features": features})
    new = SessionHotspotScorer().score({"symbol": "B", "session_features": features})
    assert old["hotspot_stage"] == "breadth-expanding"
    assert new["hotspot_stage"] == "mainline-holding"
    assert not any(s["expansion"] for s in new["hotspot_timeline"])


def test_core_first_does_not_wait_for_board_to_turn_positive():
    result = replay_hotspot_sessions([feature(0, impulse=["1"], ret5=-.02, breadth=.2)])[-1]
    assert result["active"] and result["stage"] == "emerging-watch"
    assert result["reason"] == "core-first"


def test_expansion_requires_net_new_identities():
    features = [feature(0, impulse=["1"], strong=["1"]),
                feature(1, strong=["1", "2", "3"], rs1=.01),
                feature(2, strong=["1", "2", "3"], rs1=.01)]
    result = replay_hotspot_sessions(features)
    assert result[1]["expansion"] and not result[2]["expansion"]


def test_missing_data_freezes_weak_count_and_breaks_delta_comparison():
    features = [feature(0, impulse=["1"]), feature(1), feature(2, strong=["1", "2", "3"], rs1=.02)]
    features[1]["coverage_state"] = "missing-session"
    result = replay_hotspot_sessions(features)
    assert result[1]["stage"] == "data-interrupted" and result[1]["active"]
    assert result[2]["observed_sessions"] == 2
    assert not result[2]["expansion"]


def test_normal_pullback_retains_core_but_repeated_failure_ends():
    features = [feature(0, impulse=["1"]), feature(1, ret1=-.02, healthy=["1"])]
    result = replay_hotspot_sessions(features)
    assert result[-1]["stage"] == "diverging" and result[-1]["leader_symbols"] == ["1"]
    features += [feature(i, ret1=-.04, ret5=-.1, breadth=.1) for i in range(2, 8)]
    assert not replay_hotspot_sessions(features)[-1]["active"]


def test_prefix_replay_does_not_depend_on_review_schedule_or_future_suffix():
    features = [feature(i, impulse=["1"] if i == 0 else (), healthy=["1"]) for i in range(15)]
    full = replay_hotspot_sessions(features)
    assert [replay_hotspot_sessions(features[:i+1])[-1] for i in [0, 7, 14]] == [full[i] for i in [0, 7, 14]]
    assert replay_hotspot_sessions(features[:8]) == full[:8]
    bad = deepcopy(features)
    bad[-1]["effective_date"] = bad[-2]["effective_date"]
    with pytest.raises(ValueError):
        replay_hotspot_sessions(bad)
