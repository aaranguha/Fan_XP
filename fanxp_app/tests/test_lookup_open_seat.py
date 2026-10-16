"""
Tests for fanxp_api.lookup_open_seat() — the server-side price lookup that
request_nfl_seat() calls for any real game (one with a `game_date`), per
CLAUDE.md section 6: "the claim price is looked up server-side... a seller
release beats a no-show, and an unlisted seat gets a 409." This is the one
thing standing between a fan's checkout price and whatever number their
browser happened to send — the client's own `price` field is only trusted on
the price-less preview map, never for a real game. Had zero test coverage
before this file.

The things worth protecting here:
  - A seller's own release for a seat always wins over a no-show row for the
    same seat, and returns the seller's own asking price with a release_id
    (so the request can be tied back to that release).
  - Falling back to a confirmed no-show's resale price, with release_id=None,
    when there's no matching release.
  - Section/row/seat matching is case/whitespace-insensitive (via norm()/
    seat_key()) but exact otherwise — a release or no-show for a different
    section that happens to share the same seat number must not match.
  - A no-show row with no real price (None or "") is not treated as an open
    seat — the fan can't claim a seat Fan XP never priced.
  - No game found for that league/team/date short-circuits to None (and so a
    409) without even checking no_shows.
  - A missing `seat_releases` table (accounts migration not yet applied, per
    CLAUDE.md section 6) degrades to the no-show-only path instead of raising.

Calls lookup_open_seat() directly against a minimal fake Supabase client that
filters rows the way Postgrest would (including the dotted
`seller_seats.team_slug`-style embedded-table filters the real query uses),
so these tests exercise the same filter/match logic as production, not a
mocked-out shortcut.
"""

from types import SimpleNamespace

import pytest

import fanxp_api


# ── Fake Supabase client ─────────────────────────────────────────────────────

class FakeTable:
    def __init__(self, name, data_by_table, raise_tables):
        self.name = name
        self.data_by_table = data_by_table
        self.raise_tables = raise_tables
        self.filters = {}
        self._limit = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, field, value):
        self.filters[field] = value
        return self

    def limit(self, n):
        self._limit = n
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
        if self._limit is not None:
            rows = rows[: self._limit]
        return SimpleNamespace(data=rows)


class FakeSupabase:
    def __init__(self, data_by_table, raise_tables=()):
        self.data_by_table = data_by_table
        self.raise_tables = raise_tables

    def table(self, name):
        return FakeTable(name, self.data_by_table, self.raise_tables)


def release_row(release_id, section, row_label, seat_num, price, slug="49ers",
                 game_date="2026-10-18", status="available"):
    return {
        "id": release_id,
        "price_usd": price,
        "status": status,
        "game_date": game_date,
        "seller_seats": {
            "team_slug": slug,
            "section": section,
            "row_label": row_label,
            "seat_num": seat_num,
        },
    }


GAME_ROW = {"id": 555, "league": "nfl", "home_team": "49ers", "game_date": "2026-10-18"}


def no_show_row(section, row, seat, price_usd, game_id=GAME_ROW["id"]):
    return {"game_id": game_id, "section": section, "row": row, "seat": seat, "price_usd": price_usd}


def lookup(data_by_table, raise_tables=(), slug="49ers", game_date="2026-10-18",
           section="112", row_label="10", seat_num="5"):
    sb = FakeSupabase(data_by_table, raise_tables)
    return fanxp_api.lookup_open_seat(sb, slug, game_date, section, row_label, seat_num)


# ── Release beats no-show ────────────────────────────────────────────────────

def test_matching_release_wins_and_returns_its_own_price_and_release_id():
    result = lookup({
        "seat_releases": [release_row(77, "112", "10", "5", 42.0)],
        "games": [GAME_ROW],
        "no_shows": [no_show_row("112", "10", "5", 999.0)],  # would also match
    })

    assert result == {"price": 42.0, "release_id": 77}


def test_no_shows_are_never_consulted_once_a_release_matches():
    # If the release path crashed looking at no_shows we'd see it here; the
    # function should return straight out of the release loop.
    result = lookup({
        "seat_releases": [release_row(1, "112", "10", "5", 50.0)],
        "games": [],  # no game row at all — would blow up find_open_seat if reached
        "no_shows": [],
    })

    assert result == {"price": 50.0, "release_id": 1}


# ── No-show fallback ─────────────────────────────────────────────────────────

def test_falls_back_to_a_confirmed_no_show_price_when_no_release_matches():
    result = lookup({
        "seat_releases": [],
        "games": [GAME_ROW],
        "no_shows": [no_show_row("112", "10", "5", 88.5)],
    })

    assert result == {"price": 88.5, "release_id": None}


@pytest.mark.parametrize("price_usd", [None, ""])
def test_no_show_without_a_real_price_is_not_treated_as_open(price_usd):
    result = lookup({
        "seat_releases": [],
        "games": [GAME_ROW],
        "no_shows": [no_show_row("112", "10", "5", price_usd)],
    })

    assert result is None


def test_unknown_game_short_circuits_to_none_without_checking_no_shows():
    result = lookup({
        "seat_releases": [],
        "games": [],  # no row for this league/home_team/game_date
        "no_shows": [no_show_row("112", "10", "5", 88.5)],
    })

    assert result is None


def test_unmatched_seat_returns_none():
    result = lookup({
        "seat_releases": [release_row(1, "112", "10", "6", 50.0)],  # different seat
        "games": [GAME_ROW],
        "no_shows": [no_show_row("112", "10", "6", 88.5)],  # different seat
    })

    assert result is None


# ── Exact section/row/seat matching ──────────────────────────────────────────

def test_release_in_a_different_section_with_the_same_seat_number_does_not_match():
    # The DB-side filter only narrows by team_slug/game_date/status/seat_num;
    # the python-side seat_key() check must still reject a same-numbered seat
    # in the wrong section.
    result = lookup({
        "seat_releases": [release_row(1, "203", "C", "5", 999.0)],
        "games": [GAME_ROW],
        "no_shows": [],
    }, section="112", row_label="10", seat_num="5")

    assert result is None


def test_correct_release_is_picked_out_of_several_sharing_a_seat_number():
    result = lookup({
        "seat_releases": [
            release_row(1, "203", "C", "5", 999.0),
            release_row(2, "112", "10", "5", 42.0),
            release_row(3, "305", "A", "5", 10.0),
        ],
        "games": [GAME_ROW],
        "no_shows": [],
    }, section="112", row_label="10", seat_num="5")

    assert result == {"price": 42.0, "release_id": 2}


def test_matching_is_case_and_whitespace_insensitive():
    result = lookup({
        "seat_releases": [],
        "games": [GAME_ROW],
        "no_shows": [no_show_row(" 112", "10 ", "5", 88.5)],
    }, section="112", row_label="10", seat_num="5")

    assert result == {"price": 88.5, "release_id": None}


# ── Missing accounts schema (migration not yet applied) ────────────────────

def test_missing_seat_releases_table_falls_back_to_no_show_path_instead_of_raising():
    result = lookup({
        "games": [GAME_ROW],
        "no_shows": [no_show_row("112", "10", "5", 88.5)],
    }, raise_tables=("seat_releases",))

    assert result == {"price": 88.5, "release_id": None}


def test_missing_seat_releases_table_still_returns_none_with_no_no_show_match():
    result = lookup({
        "games": [GAME_ROW],
        "no_shows": [],
    }, raise_tables=("seat_releases",))

    assert result is None
