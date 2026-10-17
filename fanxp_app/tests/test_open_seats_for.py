"""
Tests for fanxp_accounts.open_seats_for() (and its building blocks
_games_for()/_available_releases()) — the function behind the live seat-map
data feed, GET /api/nfl/<slug>/seats. Per CLAUDE.md §4.4: "the seat map now
loads each game's open seats from the API... halftime no-shows (with real
listed prices) plus seats owners released... Verified in a real browser:
10/11 Steelers matched 2,810 of 2,811 no-shows to seats." This is the exact
data every fan sees when picking a seat on the real NFL product surface, not
the demo flow.

test_accounts.py's test_seats_endpoint_merges_no_shows_and_releases already
covers one happy path through the Flask route (one no-show seat, one release
for a *different* seat). This file calls open_seats_for() directly and adds
the edge cases that test didn't reach:
  - A release for the SAME seat as a no-show overrides its price (the
    no-show's own price must not leak through once a seller has released
    that exact seat).
  - A no-show row with no listed price (None/"") is still surfaced as an
    open seat with price=None — unlike fanxp_api.lookup_open_seat()'s
    claim-time check, which treats a price-less no-show as not open at
    all. Documented as current behavior, not asserted as correct/incorrect.
  - A no-show row with no seat value at all is skipped outright (can't key
    a seat map entry on nothing).
  - Section/row/seat matching between the no-show and release tables is
    case/whitespace-insensitive via seat_key()/norm(), same as
    lookup_open_seat()'s matching.
  - No game row for the requested date means no no-shows are loaded, but a
    seller's release for that date still shows up — releases don't depend
    on a matched game row.
  - A missing seat_releases table (the accounts migration not yet applied,
    per CLAUDE.md §6: "The public seat endpoints and the claim flow
    tolerate the tables being absent") degrades to the no-show-only path
    instead of raising, exercised directly here rather than only through
    the Flask route.
"""

from types import SimpleNamespace

import pytest

import fanxp_accounts


# ── Fake Supabase client (same shape as test_lookup_open_seat.py's) ─────────

class FakeTable:
    def __init__(self, name, data_by_table, raise_tables):
        self.name = name
        self.data_by_table = data_by_table
        self.raise_tables = raise_tables
        self.filters = {}

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, field, value):
        self.filters[field] = value
        return self

    def order(self, *_args, **_kwargs):
        return self

    def range(self, _start, _end):
        return self

    def limit(self, _n):
        return self

    def _matches(self, row):
        for key, value in self.filters.items():
            if "." in key:
                outer, inner = key.split(".", 1)
                if row.get(outer, {}).get(inner) != value:
                    return False
            elif row.get(key) != value:
                return False
        return True

    def execute(self):
        if self.name in self.raise_tables:
            raise RuntimeError(f'relation "{self.name}" does not exist')
        rows = [r for r in self.data_by_table.get(self.name, []) if self._matches(r)]
        return SimpleNamespace(data=rows)


class FakeSupabase:
    def __init__(self, data_by_table, raise_tables=()):
        self.data_by_table = data_by_table
        self.raise_tables = raise_tables

    def table(self, name):
        return FakeTable(name, self.data_by_table, self.raise_tables)


GAME_ROW = {"id": 7, "league": "nfl", "home_team": "giants",
            "game_date": "2026-10-18", "opponent": "New Orleans Saints"}


def release_row(release_id, section, row_label, seat_num, price, game_date="2026-10-18", status="available"):
    return {
        "id": release_id,
        "price_usd": price,
        "status": status,
        "game_date": game_date,
        "seller_seats": {"team_slug": "giants", "section": section,
                          "row_label": row_label, "seat_num": seat_num},
    }


def no_show_row(section, row, seat, price_usd, game_id=GAME_ROW["id"]):
    return {"game_id": game_id, "section": section, "row": row, "seat": seat, "price_usd": price_usd}


def seats_for(data_by_table, raise_tables=(), slug="giants", game_date="2026-10-18"):
    sb = FakeSupabase(data_by_table, raise_tables)
    return fanxp_accounts.open_seats_for(sb, slug, game_date)


# ── Release overrides a no-show on the SAME seat ─────────────────────────────

def test_release_on_the_same_seat_as_a_no_show_overrides_its_price():
    result = seats_for({
        "games": [GAME_ROW],
        "no_shows": [no_show_row("115", "B", "5", 120.4)],
        "seat_releases": [release_row(1, "115", "B", "5", 90)],
    })

    assert result[("115", "B", "5")] == {"price": 90, "source": "release", "release_id": 1}


def test_no_show_with_no_matching_release_keeps_its_own_price_and_source():
    result = seats_for({
        "games": [GAME_ROW],
        "no_shows": [no_show_row("115", "B", "5", 120.4)],
        "seat_releases": [],
    })

    assert result[("115", "B", "5")] == {"price": 120, "source": "no_show"}


# ── Price-less no-show rows ───────────────────────────────────────────────────

@pytest.mark.parametrize("price_usd", [None, ""])
def test_no_show_without_a_real_price_still_surfaces_with_price_none(price_usd):
    # Unlike lookup_open_seat() (claim time), which refuses to treat a
    # price-less no-show as an open seat at all, the seat-map feed still
    # lists it (just with price=None) -- documenting current behavior.
    result = seats_for({
        "games": [GAME_ROW],
        "no_shows": [no_show_row("115", "B", "5", price_usd)],
        "seat_releases": [],
    })

    assert result[("115", "B", "5")] == {"price": None, "source": "no_show"}


@pytest.mark.parametrize("seat_value", [None, ""])
def test_no_show_row_with_no_seat_value_is_skipped(seat_value):
    result = seats_for({
        "games": [GAME_ROW],
        "no_shows": [no_show_row("115", "B", seat_value, 120.4)],
        "seat_releases": [],
    })

    assert result == {}


# ── Case/whitespace-insensitive seat matching ────────────────────────────────

def test_release_matches_a_no_show_despite_case_and_whitespace_differences():
    result = seats_for({
        "games": [GAME_ROW],
        "no_shows": [no_show_row(" 115", "b ", "5", 120.4)],
        "seat_releases": [release_row(1, "115", "B", "5", 90)],
    })

    assert len(result) == 1
    assert result[("115", "B", "5")] == {"price": 90, "source": "release", "release_id": 1}


# ── No game row for the requested date ───────────────────────────────────────

def test_no_matching_game_skips_no_shows_but_releases_still_show():
    # _games_for() has no row for this date (wrong date, or the game just
    # hasn't been scraped) -- no-shows can't be loaded at all, but a
    # seller's own release doesn't depend on a matched game row.
    result = seats_for({
        "games": [GAME_ROW],  # game_date 2026-10-18, but we ask about 2026-10-25
        "no_shows": [no_show_row("115", "B", "5", 120.4)],
        "seat_releases": [release_row(1, "115", "B", "6", 90, game_date="2026-10-25")],
    }, game_date="2026-10-25")

    assert result == {("115", "B", "6"): {"price": 90, "source": "release", "release_id": 1}}


def test_no_games_at_all_and_no_releases_returns_empty_dict():
    result = seats_for({"games": [], "no_shows": [], "seat_releases": []})

    assert result == {}


# ── Missing seat_releases table (accounts migration not applied) ────────────

def test_missing_seat_releases_table_degrades_to_no_show_only_path():
    result = seats_for({
        "games": [GAME_ROW],
        "no_shows": [no_show_row("115", "B", "5", 120.4)],
    }, raise_tables=("seat_releases",))

    assert result == {("115", "B", "5"): {"price": 120, "source": "no_show"}}


def test_missing_seat_releases_table_with_no_no_shows_either_is_empty():
    result = seats_for({
        "games": [GAME_ROW],
        "no_shows": [],
    }, raise_tables=("seat_releases",))

    assert result == {}


# ── Multiple seats merge correctly ───────────────────────────────────────────

def test_multiple_no_shows_and_releases_for_distinct_seats_all_appear():
    result = seats_for({
        "games": [GAME_ROW],
        "no_shows": [
            no_show_row("115", "B", "5", 120.4),
            no_show_row("115", "B", "6", 118.0),
        ],
        "seat_releases": [release_row(1, "203", "C", "9", 42)],
    })

    assert result == {
        ("115", "B", "5"): {"price": 120, "source": "no_show"},
        ("115", "B", "6"): {"price": 118, "source": "no_show"},
        ("203", "C", "9"): {"price": 42, "source": "release", "release_id": 1},
    }


# ── _games_for() ──────────────────────────────────────────────────────────────

def test_games_for_keys_by_game_date_string_and_filters_by_slug_and_league():
    sb = FakeSupabase({
        "games": [
            {"id": 1, "league": "nfl", "home_team": "giants", "game_date": "2026-10-18", "opponent": "Saints"},
            {"id": 2, "league": "nfl", "home_team": "eagles", "game_date": "2026-10-18", "opponent": "Cowboys"},
            {"id": 3, "league": "mlb", "home_team": "giants", "game_date": "2026-10-18", "opponent": "Dodgers"},
        ]
    })

    games = fanxp_accounts._games_for(sb, "giants")

    assert set(games.keys()) == {"2026-10-18"}
    assert games["2026-10-18"]["id"] == 1


def test_games_for_no_games_returns_empty_dict():
    sb = FakeSupabase({"games": []})

    assert fanxp_accounts._games_for(sb, "giants") == {}


# ── _available_releases() ─────────────────────────────────────────────────────

def test_available_releases_returns_rows_on_success():
    sb = FakeSupabase({"seat_releases": [release_row(1, "115", "B", "5", 90)]})

    rows = fanxp_accounts._available_releases(lambda: sb.table("seat_releases").select("*"))

    assert len(rows) == 1 and rows[0]["id"] == 1


def test_available_releases_swallows_any_exception_and_returns_empty_list():
    def boom():
        raise RuntimeError('relation "seat_releases" does not exist')

    assert fanxp_accounts._available_releases(boom) == []
