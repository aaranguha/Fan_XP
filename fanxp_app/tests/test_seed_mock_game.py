"""
Tests for seed_mock_game.py's find_best_game()/pick_seats() — the one-time
setup script for the legacy demo seat flow (CLAUDE.md §6: backs
`sth_seat_assignments`, not connected to the live scraping pipeline).

These two functions have real branching logic that was never covered:
  - find_best_game(): prefers the most recent game with priced no_shows
    (price_usd not null and > 0), and falls back to the most recent game
    with ANY no_shows (even unpriced) only when no priced rows exist at all.
  - pick_seats(): pulls the n highest-priced no-show seats for a game, and
    when fewer than n priced seats exist, tops up from a second,
    price-agnostic query — merging the two lists while deduping by
    (section, row, seat) so a seat already picked via the priced query is
    never counted twice against the n-seat limit.

Driven against a fake Supabase client (same chained-query-builder style as
test_check_data_integrity.py/test_supabase_client.py) so no real network
access or credentials are needed — matches CLAUDE.md's "pure logic only, no
secrets" constraint for this QA job.
"""

import pytest

import seed_mock_game as smg


class FakeResult:
    def __init__(self, data):
        self.data = data


class NotWrapper:
    """Models supabase-py's `.not_` property: `.not_.is_(field, "null")`
    negates the is_-null filter on the SAME query object it was called from,
    then hands the query back so later chaining (`.gt(...)`) still works."""

    def __init__(self, query):
        self._query = query

    def is_(self, field, value):
        assert value == "null", "only 'is not null' is used by seed_mock_game.py"
        self._query._not_null_fields.add(field)
        return self._query


class FakeQuery:
    def __init__(self, table_name, store):
        self.table_name = table_name
        self.store = store
        self.filters = {}
        self._not_null_fields = set()
        self._gt = None
        self._order_field = None
        self._order_desc = False
        self._limit = None
        self._single = False

    def select(self, *_fields):
        return self

    def eq(self, field, value):
        self.filters[field] = value
        return self

    @property
    def not_(self):
        return NotWrapper(self)

    def gt(self, field, value):
        self._gt = (field, value)
        return self

    def order(self, field, desc=False):
        self._order_field = field
        self._order_desc = desc
        return self

    def limit(self, n):
        self._limit = n
        return self

    def single(self):
        self._single = True
        return self

    def execute(self):
        rows = self.store.get(self.table_name, [])
        out = [r for r in rows if all(r.get(k) == v for k, v in self.filters.items())]
        for field in self._not_null_fields:
            out = [r for r in out if r.get(field) is not None]
        if self._gt:
            field, val = self._gt
            out = [r for r in out if r.get(field) is not None and r.get(field) > val]
        if self._order_field:
            out = sorted(out, key=lambda r: r[self._order_field], reverse=self._order_desc)
        if self._limit is not None:
            out = out[: self._limit]
        return FakeResult(data=out)


class FakeClient:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return FakeQuery(name, self.store)


def make_client(no_shows):
    return FakeClient({"no_shows": no_shows})


def seat(game_id, section, row, seat_num, price_usd=None):
    return {
        "game_id": game_id,
        "section": section,
        "row": row,
        "seat": seat_num,
        "price_usd": price_usd,
    }


# ── find_best_game() ─────────────────────────────────────────────────────────

def test_prefers_most_recent_game_with_priced_no_shows():
    sb = make_client([
        seat(1, "101", "1", "1", price_usd=50.0),
        seat(2, "102", "2", "2", price_usd=75.0),
        seat(3, "103", "3", "3", price_usd=None),  # most recent, but unpriced
    ])

    assert smg.find_best_game(sb) == 2


def test_ignores_zero_priced_rows_when_picking_the_priced_game():
    # price_usd == 0 is excluded by the `gt("price_usd", 0)` filter — a $0
    # row doesn't count as "has real $ numbers" the way a positive price does.
    sb = make_client([
        seat(1, "101", "1", "1", price_usd=50.0),
        seat(2, "102", "2", "2", price_usd=0.0),
    ])

    assert smg.find_best_game(sb) == 1


def test_falls_back_to_any_no_show_game_when_none_are_priced():
    sb = make_client([
        seat(5, "101", "1", "1", price_usd=None),
        seat(7, "102", "2", "2", price_usd=None),
    ])

    assert smg.find_best_game(sb) == 7


def test_returns_none_when_no_no_shows_exist_at_all():
    sb = make_client([])

    assert smg.find_best_game(sb) is None


# ── pick_seats() ─────────────────────────────────────────────────────────────

def test_picks_the_n_highest_priced_seats_in_descending_order():
    sb = make_client([
        seat(1, "101", "1", "1", price_usd=50.0),
        seat(1, "102", "2", "2", price_usd=200.0),
        seat(1, "103", "3", "3", price_usd=120.0),
        seat(1, "104", "4", "4", price_usd=10.0),
    ])

    seats = smg.pick_seats(sb, 1, n=3)

    assert [s["price_usd"] for s in seats] == [200.0, 120.0, 50.0]


def test_tops_up_with_unpriced_fallback_seats_when_too_few_priced_exist():
    sb = make_client([
        seat(1, "101", "1", "1", price_usd=80.0),
        seat(1, "102", "2", "2", price_usd=None),
        seat(1, "103", "3", "3", price_usd=None),
    ])

    seats = smg.pick_seats(sb, 1, n=3)

    assert len(seats) == 3
    sections = {s["section"] for s in seats}
    assert sections == {"101", "102", "103"}


def test_fallback_does_not_duplicate_a_seat_already_picked_from_the_priced_query():
    # The fallback query has no price filter, so it can re-return the same
    # priced row seed_mock_game.py already picked. pick_seats() must dedupe
    # by (section, row, seat) rather than counting it twice toward n.
    sb = make_client([
        seat(1, "101", "1", "1", price_usd=80.0),
        seat(1, "102", "2", "2", price_usd=None),
    ])

    seats = smg.pick_seats(sb, 1, n=2)

    keys = [(s["section"], s["row"], s["seat"]) for s in seats]
    assert len(keys) == len(set(keys)), "same seat counted twice"
    assert len(seats) == 2


def test_returns_fewer_than_n_when_fewer_seats_exist_overall():
    sb = make_client([
        seat(1, "101", "1", "1", price_usd=80.0),
    ])

    seats = smg.pick_seats(sb, 1, n=3)

    assert len(seats) == 1


def test_only_pulls_seats_for_the_requested_game_id():
    sb = make_client([
        seat(1, "101", "1", "1", price_usd=80.0),
        seat(2, "201", "1", "1", price_usd=999.0),  # different game, must be ignored
    ])

    seats = smg.pick_seats(sb, 1, n=3)

    assert len(seats) == 1
    assert seats[0]["section"] == "101"


def test_default_n_is_three():
    sb = make_client([
        seat(1, "101", "1", "1", price_usd=10.0),
        seat(1, "102", "2", "2", price_usd=20.0),
        seat(1, "103", "3", "3", price_usd=30.0),
        seat(1, "104", "4", "4", price_usd=40.0),
    ])

    seats = smg.pick_seats(sb, 1)

    assert len(seats) == 3
