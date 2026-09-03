"""Tests for the month chunker that drives the incremental download."""

from datetime import date

import pytest

from gmail_bulk_export.scripts.chunking import (
    MonthChunk,
    clamp_to_today,
    iter_plan,
    month_chunks,
    order_by_priority,
    parse_month,
)


def test_parse_month_accepts_both_forms():
    assert parse_month("2024-03") == (2024, 3)
    assert parse_month("202403") == (2024, 3)
    assert parse_month("2024/03") == (2024, 3)


def test_parse_month_rejects_bad_month():
    with pytest.raises(ValueError):
        parse_month("2024-13")


def test_full_range_has_no_gaps():
    chunks = month_chunks("2016-01", "2025-12")
    assert len(chunks) == 120
    assert chunks[0].label == "2016-01"
    assert chunks[-1].label == "2025-12"

    # Every consecutive pair must be adjacent months.
    for previous, current in zip(chunks, chunks[1:]):
        expected = (
            (previous.year + 1, 1) if previous.month == 12 else (previous.year, previous.month + 1)
        )
        assert (current.year, current.month) == expected


def test_query_bounds_cover_the_whole_month():
    """`before:` is exclusive, so the query must reach past the last day."""
    february = MonthChunk(2024, 2)  # leap year
    assert february.last_day == date(2024, 2, 29)
    assert february.query_start == "2024-01-31"
    assert february.query_end == "2024-03-01"

    non_leap = MonthChunk(2023, 2)
    assert non_leap.last_day == date(2023, 2, 28)
    assert non_leap.query_end == "2023-03-01"

    december = MonthChunk(2024, 12)
    assert december.query_start == "2024-11-30"
    assert december.query_end == "2025-01-01"


def test_consecutive_chunks_overlap_rather_than_leave_gaps():
    chunks = month_chunks("2023-11", "2024-02")
    for previous, current in zip(chunks, chunks[1:]):
        # The next chunk starts before the previous one ends: overlap, not gap.
        assert current.query_start < previous.query_end


def test_checkpoint_key_is_stable_and_month_scoped():
    chunk = MonthChunk(2024, 7)
    assert chunk.checkpoint_key() == "metadata_20240701_20240731"
    # The padded query must not leak into the key, or resuming would break.
    assert chunk.checkpoint_key() == MonthChunk(2024, 7).checkpoint_key()


def test_clamp_to_today_drops_future_months():
    today = date(2026, 7, 26)
    assert clamp_to_today("2027-01", today=today) == "2026-07"
    assert clamp_to_today("2025-12", today=today) == "2025-12"


def test_priority_years_come_first():
    chunks = month_chunks("2016-01", "2025-12")
    ordered = order_by_priority(chunks, [2024, 2023, 2025])

    years_in_order = list(dict.fromkeys(chunk.year for chunk in ordered))
    assert years_in_order[:3] == [2024, 2023, 2025]
    # Everything else walks backwards from the most recent.
    assert years_in_order[3:] == [2022, 2021, 2020, 2019, 2018, 2017, 2016]
    assert len(ordered) == len(chunks)


def test_priority_preserves_month_order_within_a_year():
    ordered = order_by_priority(month_chunks("2024-01", "2024-12"), [2024])
    assert [chunk.month for chunk in ordered] == list(range(1, 13))


def test_plan_finishes_a_year_across_all_mailboxes_before_moving_on():
    chunks = order_by_priority(month_chunks("2023-01", "2024-12"), [2024, 2023])
    plan = list(iter_plan(["a@x.com", "b@x.com"], chunks))

    assert len(plan) == 2 * 24
    years_in_order = list(dict.fromkeys(chunk.year for _, chunk in plan))
    assert years_in_order == [2024, 2023]
    # All of 2024 for both mailboxes lands before any of 2023.
    first_2023 = next(i for i, (_, chunk) in enumerate(plan) if chunk.year == 2023)
    assert all(chunk.year == 2024 for _, chunk in plan[:first_2023])


def test_inverted_range_is_rejected():
    with pytest.raises(ValueError):
        month_chunks("2025-01", "2024-01")
