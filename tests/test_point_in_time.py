from __future__ import annotations

import pandas as pd
import pytest

from accounting_red_flags.point_in_time.reports import annual_rows, select_visible_revisions
from accounting_red_flags.point_in_time.universe import filter_a_share_universe, select_industries


def _report(symbol, quarter, date, **values):
    row = {"symbol": symbol, "quarter": quarter, "date": date, "if_adjusted": 0}
    row.update(values)
    return row


def test_future_report_is_never_visible():
    frame = pd.DataFrame(
        [
            _report("600001.SH", "2024q4", "20250430", is_revenue=100.0),
            _report("600001.SH", "2024q4", "20250501", is_revenue=200.0),
        ]
    )
    visible, conflicts = select_visible_revisions(frame, "20250430")
    assert len(visible) == 1
    assert visible.iloc[0]["is_revenue"] == 100.0
    assert conflicts == set()


def test_latest_visible_revision_wins():
    frame = pd.DataFrame(
        [
            _report("600001.SH", "2024q4", "20250430", is_revenue=100.0),
            _report("600001.SH", "2024q4", "20250429", is_revenue=999.0),
        ]
    )
    visible, _ = select_visible_revisions(frame, "20251231")
    assert len(visible) == 1
    assert visible.iloc[0]["is_revenue"] == 100.0


def test_conflicting_same_day_revisions_are_flagged():
    frame = pd.DataFrame(
        [
            _report("600001.SH", "2024q4", "20250430", is_revenue=100.0),
            _report("600001.SH", "2024q4", "20250430", is_revenue=200.0),
        ]
    )
    visible, conflicts = select_visible_revisions(frame, "20251231")
    assert len(visible) == 1
    assert conflicts == {("600001.SH", "2024q4")}


def test_identical_duplicate_is_not_a_conflict():
    frame = pd.DataFrame(
        [
            _report("600001.SH", "2024q4", "20250430", is_revenue=100.0),
            _report("600001.SH", "2024q4", "20250430", is_revenue=100.0),
        ]
    )
    visible, conflicts = select_visible_revisions(frame, "20251231")
    assert len(visible) == 1
    assert conflicts == set()


def test_missing_required_columns_raise():
    frame = pd.DataFrame([{"symbol": "600001.SH"}])
    with pytest.raises(ValueError, match="missing columns"):
        select_visible_revisions(frame, "20251231")


def test_all_future_frame_is_empty():
    frame = pd.DataFrame([_report("600001.SH", "2024q4", "20260101", is_revenue=1.0)])
    visible, conflicts = select_visible_revisions(frame, "20251231")
    assert visible.empty
    assert conflicts == set()


def test_annual_rows_only_q4_sorted_and_truncated():
    frame = pd.DataFrame(
        [
            _report("600001.SH", "2024q4", "20250430", is_revenue=400.0),
            _report("600001.SH", "2023q4", "20240430", is_revenue=300.0),
            _report("600001.SH", "2024q2", "20240830", is_revenue=200.0),
            _report("600001.SH", "2022q4", "20230430", is_revenue=200.0),
            _report("600001.SH", "2021q4", "20220430", is_revenue=100.0),
        ]
    )
    visible, _ = select_visible_revisions(frame, "20251231")
    rows = annual_rows(visible, "600001.SH", max_years=2)
    assert [row["year"] for row in rows] == [2023, 2024]
    assert rows[-1]["revenue"] == 400.0
    assert rows[-1]["quarter"] == "2024q4"
    assert rows[-1]["announce_date"] == "20250430"


def test_annual_rows_field_fallback():
    frame = pd.DataFrame(
        [
            {
                "symbol": "600001.SH",
                "quarter": "2024q4",
                "date": "20250430",
                "if_adjusted": 1,
                "is_total_revenue": 500.0,
                "is_total_cogs": 300.0,
                "bs_notes_accts_receiv": 42.0,
            }
        ]
    )
    visible, _ = select_visible_revisions(frame, "20251231")
    rows = annual_rows(visible, "600001.SH", max_years=5)
    assert rows[0]["revenue"] == 500.0
    assert rows[0]["operating_cost"] == 300.0
    assert rows[0]["accounts_receivable"] == 42.0
    assert rows[0]["net_profit"] is None
    assert rows[0]["if_adjusted"] == 1


def test_annual_rows_unknown_symbol_is_empty():
    frame = pd.DataFrame([_report("600001.SH", "2024q4", "20250430", is_revenue=1.0)])
    assert annual_rows(frame, "600999.SH", max_years=5) == []


def test_universe_is_point_in_time():
    frame = pd.DataFrame(
        [
            {"symbol": "600001.SH", "listed_date": "20100101", "de_listed_date": None},
            {"symbol": "600002.SH", "listed_date": "20260101", "de_listed_date": None},
            {"symbol": "600003.SH", "listed_date": "20100101", "de_listed_date": "20250601"},
            {"symbol": "600004.SH", "listed_date": "20100101", "de_listed_date": "20260101"},
            {"symbol": "000001.SZ", "listed_date": "20100101", "de_listed_date": None},
            {"symbol": "12345.HK", "listed_date": "20100101", "de_listed_date": None},
        ]
    )
    universe = filter_a_share_universe(frame, "20251231")
    assert universe == ["000001.SZ", "600001.SH", "600004.SH"]


def test_universe_empty_frame():
    assert filter_a_share_universe(pd.DataFrame(), "20251231") == []


def test_industries_pick_assignment_valid_on_date():
    constituents = pd.DataFrame(
        [
            {"stock_symbol": "600001.SH", "l1_code": "801120", "in_date": "20100101", "out_date": "20240101"},
            {"stock_symbol": "600001.SH", "l1_code": "801080", "in_date": "20240101", "out_date": None},
            {"stock_symbol": "600002.SH", "l1_code": "801780", "in_date": "20100101", "out_date": None},
        ]
    )
    details = pd.DataFrame(
        [
            {"industry_code": "801120", "industry_name": "食品饮料"},
            {"industry_code": "801080", "industry_name": "电子"},
            {"industry_code": "801780", "industry_name": "银行"},
        ]
    )
    mapping = select_industries(constituents, details, "20251231")
    assert mapping["600001.SH"] == {"industry_code": "801080", "industry_name": "电子"}
    assert mapping["600002.SH"] == {"industry_code": "801780", "industry_name": "银行"}


def test_ambiguous_industry_assignment_is_dropped():
    constituents = pd.DataFrame(
        [
            {"stock_symbol": "600001.SH", "l1_code": "801120", "in_date": "20100101", "out_date": None},
            {"stock_symbol": "600001.SH", "l1_code": "801080", "in_date": "20100101", "out_date": None},
        ]
    )
    details = pd.DataFrame([{"industry_code": "801120", "industry_name": "食品饮料"}])
    assert select_industries(constituents, details, "20251231") == {}


def test_industries_empty_constituents():
    assert select_industries(pd.DataFrame(), pd.DataFrame(), "20251231") == {}
