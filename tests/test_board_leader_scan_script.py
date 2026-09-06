from scripts import scan_board_leaders


def test_apply_splits_legacy_group_and_preserves_its_id(monkeypatch):
    requests: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_request(base_url, path, method, payload=None):
        requests.append((path, method, payload))
        if path == "/api/custom-groups":
            if method == "GET":
                return {"items": [{"id": "legacy-id", "name": "全市场辨识度品种"}]}
            return {"id": "historical-id", **(payload or {})}
        if path == "/api/instrument-tags?symbol=A.SZ&symbol=B.SZ":
            return {"items": [
                {"symbol": "A.SZ", "tags": ["板块龙1", "人工标签"]},
                {"symbol": "B.SZ", "tags": []},
            ]}
        return {}

    monkeypatch.setattr(scan_board_leaders, "_request", fake_request)
    assignments = [
        _assignment("recent", "A.SZ", 1),
        _assignment("recent", "B.SZ", 2),
        _assignment("historical", "A.SZ", 2),
        _assignment("historical", "B.SZ", 1),
    ]

    scan_board_leaders._apply(
        "http://test",
        "近期辨识度品种",
        "历史辨识度品种",
        assignments,
        {"as_of_date": "2026-09-04"},
        ["A.SZ", "B.SZ"],
    )

    legacy_update = next(item for item in requests if item[:2] == ("/api/custom-groups/legacy-id", "PUT"))
    assert legacy_update[2]["name"] == "近期辨识度品种"
    assert [item["symbol"] for item in legacy_update[2]["members"]] == ["A.SZ"]
    historical_create = next(item for item in requests if item[:2] == ("/api/custom-groups", "POST"))
    assert historical_create[2]["name"] == "历史辨识度品种"
    assert {item["symbol"] for item in historical_create[2]["members"]} == {"A.SZ", "B.SZ"}
    a_tags = next(item for item in requests if item[:2] == ("/api/instruments/A.SZ/tags", "PUT"))[2]["tags"]
    assert "板块龙1" not in a_tags
    assert {"人工标签", "近期板块龙1", "历史板块龙2"}.issubset(a_tags)


def test_apply_legacy_report_does_not_touch_historical_group(monkeypatch):
    requests: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_request(base_url, path, method, payload=None):
        requests.append((path, method, payload))
        if path.startswith("/api/instrument-tags?"):
            return {"items": []}
        if path == "/api/custom-groups" and method == "GET":
            return {"items": [{"id": "recent-id", "name": "近期辨识度品种"}]}
        return {}

    monkeypatch.setattr(scan_board_leaders, "_request", fake_request)
    scan_board_leaders._apply(
        "http://test",
        "近期辨识度品种",
        "历史辨识度品种",
        [_assignment("recent", "A.SZ", 1)],
        {"as_of_date": "2026-09-04"},
        ["A.SZ"],
    )

    group_writes = [item for item in requests if item[0].startswith("/api/custom-groups") and item[1] != "GET"]
    assert len(group_writes) == 1
    assert group_writes[0][2]["name"] == "近期辨识度品种"


def _assignment(profile: str, symbol: str, rank: int) -> dict[str, object]:
    return {
        "profile": profile,
        "member_symbol": symbol,
        "rank": rank,
        "board_name": "机器人",
        "score": 0.8 - rank * 0.1,
    }
