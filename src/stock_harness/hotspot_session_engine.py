"""One causal trading-session kernel for online discovery and offline replay."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import ceil

VERSION = "board-hotspot-emergence-v9-session-tracking"
LABELS = {"emerging-watch": "萌芽观察", "breadth-expanding": "新增扩散",
          "mainline-holding": "主线维持", "diverging": "分歧调整",
          "exhausted": "退潮观察", "ended": "已结束", "failed": "暂无候选",
          "data-interrupted": "证据中断"}


def replay_hotspot_sessions(features: Sequence[Mapping]) -> list[dict]:
    dates = [str(f.get("effective_date") or "") for f in features]
    if not all(dates) or dates != sorted(set(dates)):
        raise ValueError("hotspot sessions must have unique increasing dates")
    timeline = []
    previous_strong = None
    active, weak, age, episode = False, 0, 0, 0
    started = None
    cores: dict[str, int] = {}
    core_first: dict[str, str] = {}
    expansion_days: list[bool] = []
    ever_expanded = False
    for feature, day in zip(features, dates):
        m = feature.get("metrics") or {}
        members = feature.get("member_snapshot") or {}
        n = int(members.get("member_count") or 0)
        covered = int(members.get("return_5_covered_count") or 0)
        valid = feature.get("coverage_state") == "complete" and n >= 3 and covered / n >= .65
        if not valid:
            timeline.append(dict(effective_date=day, stage="data-interrupted", active=active,
                                 first_detected=started, episode=episode, observed_sessions=age,
                                 leader_symbols=sorted(cores), new_strong=[], lost_strong=[],
                                 core_first_observed={s: core_first[s] for s in cores},
                                 expansion=False, reason="coverage-interrupted", score=0))
            # Do not call recovered observations a one-session change.
            previous_strong = None
            expansion_days = []
            continue
        returns, relative = m.get("returns") or {}, m.get("relative_strength") or {}
        ret1, ret5 = float(returns.get("1") or 0), float(returns.get("5") or 0)
        rs1, rs5 = float(relative.get("1") or 0), float(relative.get("5") or 0)
        strong = set(members.get("strong_member_symbols") or [])
        impulses = set(members.get("impulse_leader_symbols") or [])
        healthy = set(members.get("healthy_member_symbols") or [])
        valid_symbols = set(members.get("covered_member_symbols") or [])
        new = strong - previous_strong if previous_strong is not None else set()
        lost = previous_strong - strong if previous_strong is not None else set()
        breadth = float(members.get("positive_return_1_ratio") or 0)
        broad_seed = ret1 >= .01 and rs1 >= .005 and breadth >= .65 and float(m.get("volume_ratio20") or 0) >= 1.1
        expansion = (previous_strong is not None and len(new) - len(lost) >= max(2, ceil(covered * .05))
                     and breadth >= .55 and rs1 >= .004)
        expansion_days.append(expansion)
        expansion_days = expansion_days[-3:]
        for symbol in list(cores):
            if symbol not in valid_symbols:
                continue  # Missing stock data is not evidence of leadership loss.
            cores[symbol] = 0 if symbol in healthy or symbol in impulses else cores[symbol] + 1
            if cores[symbol] >= 3:
                del cores[symbol]
        seed = bool(impulses) or broad_seed
        if seed and not active:
            active, weak, age = True, 0, 0
            episode += 1
            started = day
            ever_expanded = False
            cores = {}
            core_first = {}
        if active:
            for symbol in impulses:
                core_first.setdefault(symbol, day)
            cores.update({symbol: 0 for symbol in impulses})
            age += 1
        supported = bool(cores) or (ret5 > 0 and rs5 > 0 and float(members.get("positive_return_5_ratio") or 0) >= .45)
        reason, stage = "no-core-or-broad-impulse", "failed"
        if active:
            if ret1 <= -.035 or (not seed and not supported):
                weak += 1
                stage = "ended" if weak >= 5 else "exhausted" if weak >= 3 else "diverging"
                reason = "sharp-reversal" if ret1 <= -.035 else "support-weakened"
                if stage == "ended":
                    active = False
                    cores = {}
            else:
                weak = 0
                if expansion:
                    stage, reason = "breadth-expanding", "net-new-strong-members"
                    ever_expanded = ever_expanded or sum(expansion_days) >= 2
                elif ret1 < -.01:
                    stage, reason = "diverging", "pullback-core-retained"
                elif ever_expanded or age >= 3:
                    stage, reason = "mainline-holding", "strength-maintained-not-new-diffusion"
                else:
                    stage, reason = "emerging-watch", "core-first" if impulses else "broad-impulse"
        score = (min(25, max(0, rs5) * 250) + min(20, len(cores) * 5)
                 + min(25, max(0, len(new) - len(lost)) / max(1, covered) * 250)
                 + min(20, breadth * 20) + (10 if expansion else 0))
        timeline.append(dict(effective_date=day, stage=stage, active=active,
            first_detected=started, episode=episode, observed_sessions=age,
            leader_symbols=sorted(cores), new_strong=sorted(new), lost_strong=sorted(lost),
            core_first_observed={s: core_first[s] for s in cores},
            expansion=expansion, reason=reason, score=round(score, 2), weak_sessions=weak,
            return_5=ret5, relative_strength_5=rs5, breadth=breadth))
        previous_strong = strong
    return timeline


class SessionHotspotScorer:
    system_id = "board-hotspot-emergence"
    version = VERSION
    entity_scope = "board"

    def score(self, entity: Mapping) -> dict:
        history = replay_hotspot_sessions(entity.get("session_features") or [])
        last = history[-1] if history else dict(stage="data-interrupted", active=False,
                   score=0, reason="coverage-interrupted", leader_symbols=[])
        stage = last["stage"]
        active = bool(last["active"])
        risk = stage in {"diverging", "exhausted", "ended", "data-interrupted"}
        reason_labels = {"core-first": "核心先行，板块尚待确认", "broad-impulse": "板块同步异动，等待持续性",
            "net-new-strong-members": "强势成员净增加", "support-weakened": "支持减弱",
            "strength-maintained-not-new-diffusion": "已有强度维持，非新增扩散",
            "pullback-core-retained": "板块回踩，核心身份保留", "sharp-reversal": "当日显著回落",
            "coverage-interrupted": "数据不足，暂停状态判断", "no-core-or-broad-impulse": "暂无核心或板块同步异动"}
        current_members = ((entity.get("session_features") or [{}])[-1].get("member_snapshot") or {})
        current_symbols = set(current_members.get("covered_member_symbols") or [])
        missing = sorted(set(last["leader_symbols"]) - current_symbols)
        risk = risk or bool(missing)
        return dict(symbol=entity["symbol"], eligible=active, total_score=last["score"], raw_score=last["score"],
            grade="C" if risk else "B" if active else "D", verdict=LABELS[stage],
            summary=reason_labels[last["reason"]], risk_summary="核心行情缺失，身份仅保留" if missing else "观察候选，非交易许可" if active else "",
            penalties=[], hard_events=[], disqualifiers=[] if active else [last["reason"]], components={},
            hotspot_stage=stage, observation_only=True, session_tracking=True,
            leader_symbols=last["leader_symbols"], first_detected=last.get("first_detected"),
            tracking_sessions=last.get("observed_sessions", 0), tracking_window_start=history[0]["effective_date"] if history else None,
            tracking_left_censored=bool(history and last.get("first_detected") == history[0]["effective_date"]),
            missing_leader_symbols=missing, core_first_observed=last.get("core_first_observed", {}),
            new_strong_count=len(last.get("new_strong", [])), lost_strong_count=len(last.get("lost_strong", [])),
            new_strong_symbols=last.get("new_strong", []), lost_strong_symbols=last.get("lost_strong", []),
            change_bucket="risk" if risk else "new" if last.get("first_detected") == last.get("effective_date") else "strengthening" if last.get("expansion") else "maintaining",
            hotspot_timeline=history[-10:], membership_semantics="current-active-membership",
            risk_visible=risk and bool(last.get("first_detected")))
