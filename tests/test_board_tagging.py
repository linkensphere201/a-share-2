from stock_harness.board_tagging import BoardTagCandidate, select_board_tags


def candidate(
    board_id: int, symbol: str, name: str, classification: str,
    source: str, member_count: int, exchange: str = "DC",
) -> BoardTagCandidate:
    return BoardTagCandidate(
        board_id, symbol, name, classification, source, exchange, member_count
    )


def test_selects_one_structured_industry_and_two_specific_concepts():
    selected = select_board_tags([
        candidate(1, "BK-I-EM", "Semiconductor", "industry", "eastmoney", 210),
        candidate(2, "801080.SI", "Electronics", "industry", "sw", 500, "SI"),
        candidate(3, "BK-CPO", "CPO", "concept", "eastmoney", 80),
        candidate(4, "885-CPO", "CPO concept", "concept", "ths", 82, "TI"),
        candidate(5, "BK-HBM", "HBM", "concept", "eastmoney", 35),
        candidate(6, "BK-MARGIN", "Margin financing", "concept", "eastmoney", 3000),
        candidate(7, "885-EVENT", "2026一季报预增", "concept", "ths", 60, "TI"),
    ])

    assert len(selected) == 3
    assert [item.candidate.classification for item in selected] == [
        "industry", "concept", "concept",
    ]
    assert selected[0].candidate.symbol == "801080.SI"
    assert {item.candidate.name for item in selected[1:]} == {"CPO", "HBM"}
    assert len({item.candidate.board_id for item in selected}) == 3


def test_empty_or_unclassified_memberships_produce_no_tags():
    assert select_board_tags([]) == []
    assert select_board_tags([
        candidate(1, "OTHER", "Other", "sector", "other", 20),
    ]) == []
