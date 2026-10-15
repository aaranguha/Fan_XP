"""
Tests for check_data_integrity.py's fetch_no_show_ids() cursor pagination.

fetch_no_show_ids() pages through the no_shows table in batches of 1000,
using `gt("id", last_id)` + `order("id")` as a cursor instead of OFFSET
(the module docstring explains why: unstable row ordering across paginated
OFFSET calls, and a statement timeout on one big unfiltered scan). That
cursor loop — advancing last_id to the last row of each page and stopping
once a page comes back shorter than the page size — is pure logic over
whatever the fake Supabase client returns, but no existing test ever drives
a game past 1000 no_shows rows before fetch_no_show_ids runs (the one test
with 100,000 rows uses a --league filter that excludes the game before its
no_shows are ever queried), so the loop itself has never actually been
exercised across more than one page.
"""

import sys

import pytest

import check_data_integrity as cdi

from test_check_data_integrity import FakeClient, FakeQuery, make_store, _listing_rows, _no_show_rows  # noqa: E402


def test_fetch_no_show_ids_paginates_across_multiple_pages():
    # 2500 rows needs 3 pages at page size 1000 (1000 + 1000 + 500).
    no_shows = _no_show_rows(1, "nfl", 2500, start_id=0)
    client = FakeClient(make_store([], [], no_shows))

    ids = cdi.fetch_no_show_ids(client, game_id=1, league="nfl")

    assert len(ids) == 2500
    assert set(ids) == set(range(2500))
    # Cursor order must come back sorted ascending (order("id") per page).
    assert ids == sorted(ids)


def test_fetch_no_show_ids_exact_multiple_of_page_size_terminates():
    # Exactly 2000 rows == 2 full pages. The loop must still terminate (the
    # 3rd fetch comes back empty) rather than looping forever or re-adding
    # the last page because the cursor didn't advance correctly.
    no_shows = _no_show_rows(1, "nfl", 2000, start_id=0)
    client = FakeClient(make_store([], [], no_shows))

    ids = cdi.fetch_no_show_ids(client, game_id=1, league="nfl")

    assert len(ids) == 2000
    assert set(ids) == set(range(2000))


def test_fetch_no_show_ids_filters_by_game_and_league():
    no_shows = (
        _no_show_rows(1, "nfl", 1500, start_id=0)
        + _no_show_rows(2, "nfl", 10, start_id=100000)
        + _no_show_rows(1, "mlb", 10, start_id=200000)
    )
    client = FakeClient(make_store([], [], no_shows))

    ids = cdi.fetch_no_show_ids(client, game_id=1, league="nfl")

    assert len(ids) == 1500
    assert set(ids) == set(range(1500))


def test_fetch_no_show_ids_empty_table_returns_empty_list():
    client = FakeClient(make_store([], [], []))

    assert cdi.fetch_no_show_ids(client, game_id=1, league="nfl") == []


# ── End-to-end: a corrupted game with more than one page of no_shows ───────

def test_main_deletes_all_rows_for_a_corrupted_game_past_1000_rows(monkeypatch):
    # Regression check for the pagination + 200-id-batch delete working
    # together: a corrupted game with 1500 no_shows (> the fetch page size
    # of 1000, and > the delete batch size of 200) must have every row
    # deleted, not just the first page or the first delete batch.
    games = [{"id": 1, "league": "nfl", "home_team": "49ers", "opponent": "rams", "game_date": "2026-09-13"}]
    listings = _listing_rows(1, "nfl", "pre_game", 100) + _listing_rows(1, "nfl", "halftime", 80)
    no_shows = _no_show_rows(1, "nfl", 1500, start_id=0) + _no_show_rows(2, "nfl", 20, start_id=100000)

    client = FakeClient(make_store(games + [{"id": 2, "league": "nfl", "home_team": "x", "opponent": "y", "game_date": "2026-09-13"}], listings, no_shows))
    monkeypatch.setattr(cdi, "_get_client", lambda: client)
    monkeypatch.setattr(sys, "argv", ["check_data_integrity.py", "--league", "nfl"])

    assert cdi.main() == 1

    remaining = client.store["no_shows"]
    assert all(r["game_id"] != 1 for r in remaining)
    # Game 2's no_shows (20, within the plausible range vs. no snapshots
    # recorded here so it's skipped as "can't evaluate") must be untouched.
    assert sum(1 for r in remaining if r["game_id"] == 2) == 20
