"""
Tests for the read-side of fanxp_api.py's generic/demo flow (CLAUDE.md
section 6, point 1): demo_game_id(), find_price(), latest_surrender(),
GET /api/empty-seats (empty_seats()), and GET /api/requests/<id>
(request_status()).

This flow backs the now-orphaned docs/fanxp_app.html -- nothing on the live
site links to it anymore, and CLAUDE.md explicitly says to treat it as
legacy/demo-only, not the real product surface. But it is still live code
shipped in fanxp_api.py (deployed on Render), it had zero test coverage
before this file, and it is pure logic once Supabase is faked out, so it's
worth a safety net like the rest of this module.

Three helpers worth protecting on their own:
  - demo_game_id(): "most recent" means highest game_id, not first-inserted
    or last-in-list -- a wrong ORDER BY here would silently show fans the
    wrong game's seats.
  - find_price(): filters by section/row/seat cast to str(), so a seat
    dict carrying ints (as the DB driver can hand back) must still match a
    no_shows row stored with string columns.
  - latest_surrender(): "latest" means highest id, and must not leak a
    surrender belonging to a different seat_id/game_id combination.

And two routes that assemble those pieces:
  - empty_seats(): must default a seat with no surrender row to status
    "available" with surrender_id None (not KeyError/None-crash), and must
    short-circuit to {"game": None, "seats": []} when no demo game is
    seeded at all, before ever touching the "games" table.
  - request_status(): the pass_code key must appear only when the
    surrender is actually "sold" AND an auction AND a gate_pass both
    exist for it -- any other combination must omit the key entirely
    rather than send a stale/empty one.

Driven by calling the route functions directly inside
app.test_request_context(), against a minimal fake Supabase client -- same
shape used elsewhere in this test package (see test_fanxp_api.py) -- so no
real network access or credentials are needed. Every case here always has
exactly one matching row for any `.single()` call in the code path it
exercises, so this file doesn't need to model (and doesn't assert on)
postgrest's real "zero/multiple rows" behavior for `.single()`, which
differs from `.maybe_single()` (see fetch_one()'s own docstring and
CLAUDE.md section 4.3).
"""

import pytest

import fanxp_api


# ── Fake Supabase client (same shape as test_fanxp_api.py's) ────────────────

class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, table_name, store):
        self.table_name = table_name
        self.store = store
        self.filters = {}
        self._limit = None
        self._order = None
        self._desc = False
        self._mode = "select"
        self._payload = None
        self._single = False

    def select(self, *_args, **_kwargs):
        self._mode = "select"
        return self

    def insert(self, payload):
        self._mode = "insert"
        self._payload = payload
        return self

    def eq(self, field, value):
        self.filters[field] = value
        return self

    def order(self, field, desc=False):
        self._order = field
        self._desc = desc
        return self

    def limit(self, n):
        self._limit = n
        return self

    def single(self):
        self._single = True
        return self

    def _matching(self):
        rows = self.store.get(self.table_name, [])
        out = [r for r in rows if all(r.get(k) == v for k, v in self.filters.items())]
        if self._order:
            out = sorted(out, key=lambda r: r[self._order], reverse=self._desc)
        return out

    def execute(self):
        if self._mode == "insert":
            row = dict(self._payload)
            self.store.setdefault(self.table_name, []).append(row)
            return FakeResult([row])

        matching = self._matching()
        limited = matching[: self._limit] if self._limit is not None else matching

        if self._single:
            return FakeResult(limited[0])

        return FakeResult(limited)


class FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return FakeQuery(name, self.store)


@pytest.fixture
def store():
    return {}


@pytest.fixture
def sb(store):
    return FakeSupabase(store)


@pytest.fixture
def fake_supabase(sb, monkeypatch):
    monkeypatch.setattr(fanxp_api, "get_supabase", lambda: sb)
    return sb


# ── demo_game_id() ───────────────────────────────────────────────────────────

def test_demo_game_id_returns_none_when_nothing_seeded(sb):
    assert fanxp_api.demo_game_id(sb) is None


def test_demo_game_id_returns_the_highest_game_id(store, sb):
    store["sth_seat_assignments"] = [
        {"game_id": 3, "seat_id": 1},
        {"game_id": 7, "seat_id": 2},
        {"game_id": 5, "seat_id": 3},
    ]
    assert fanxp_api.demo_game_id(sb) == 7


# ── find_price() ─────────────────────────────────────────────────────────────

def test_find_price_returns_none_when_no_matching_no_show(store, sb):
    seat = {"section": "112", "row": "10", "seat": "5"}
    assert fanxp_api.find_price(sb, game_id=1, seat=seat) is None


def test_find_price_returns_the_price_on_a_match(store, sb):
    store["no_shows"] = [
        {"game_id": 1, "section": "112", "row": "10", "seat": "5", "price_usd": 85.0},
    ]
    seat = {"section": "112", "row": "10", "seat": "5"}
    assert fanxp_api.find_price(sb, game_id=1, seat=seat) == 85.0


def test_find_price_casts_seat_fields_to_str_for_the_match(store, sb):
    # The DB driver can hand back a seat dict with int-typed section/row/seat
    # (e.g. straight from the "seats" table join), while no_shows stores them
    # as strings. find_price() must cast with str() to still match.
    store["no_shows"] = [
        {"game_id": 1, "section": "112", "row": "10", "seat": "5", "price_usd": 85.0},
    ]
    seat = {"section": 112, "row": 10, "seat": 5}
    assert fanxp_api.find_price(sb, game_id=1, seat=seat) == 85.0


def test_find_price_does_not_match_a_different_games_no_show(store, sb):
    store["no_shows"] = [
        {"game_id": 2, "section": "112", "row": "10", "seat": "5", "price_usd": 85.0},
    ]
    seat = {"section": "112", "row": "10", "seat": "5"}
    assert fanxp_api.find_price(sb, game_id=1, seat=seat) is None


# ── latest_surrender() ───────────────────────────────────────────────────────

def test_latest_surrender_returns_none_when_none_exists(store, sb):
    assert fanxp_api.latest_surrender(sb, seat_id=1, game_id=1) is None


def test_latest_surrender_returns_the_highest_id_row(store, sb):
    store["seat_surrenders"] = [
        {"id": 10, "seat_id": 1, "game_id": 1, "status": "detected"},
        {"id": 12, "seat_id": 1, "game_id": 1, "status": "sth_pinged"},
        {"id": 11, "seat_id": 1, "game_id": 1, "status": "declined"},
    ]
    result = fanxp_api.latest_surrender(sb, seat_id=1, game_id=1)
    assert result["id"] == 12
    assert result["status"] == "sth_pinged"


def test_latest_surrender_does_not_leak_a_different_seats_row(store, sb):
    store["seat_surrenders"] = [
        {"id": 99, "seat_id": 2, "game_id": 1, "status": "sold"},
    ]
    assert fanxp_api.latest_surrender(sb, seat_id=1, game_id=1) is None


def test_latest_surrender_does_not_leak_a_different_games_row(store, sb):
    store["seat_surrenders"] = [
        {"id": 99, "seat_id": 1, "game_id": 2, "status": "sold"},
    ]
    assert fanxp_api.latest_surrender(sb, seat_id=1, game_id=1) is None


# ── GET /api/empty-seats ─────────────────────────────────────────────────────

def _get_empty_seats():
    with fanxp_api.app.test_request_context("/api/empty-seats"):
        resp = fanxp_api.empty_seats()
    return resp.get_json()


def test_empty_seats_returns_null_game_when_no_demo_game_seeded(fake_supabase):
    data = _get_empty_seats()
    assert data == {"game": None, "seats": []}


def test_empty_seats_assembles_price_and_status_from_the_helpers(store, fake_supabase):
    store["sth_seat_assignments"] = [
        {"game_id": 1, "seat_id": 1, "seats": {"id": 1, "section": "112", "row": "10", "seat": "5"}},
    ]
    store["games"] = [{"id": 1, "opponent": "Rams", "game_date": "2026-09-13", "arena": "Levi's Stadium"}]
    store["no_shows"] = [{"game_id": 1, "section": "112", "row": "10", "seat": "5", "price_usd": 85.0}]
    store["seat_surrenders"] = [
        {"id": 5, "seat_id": 1, "game_id": 1, "status": "sth_pinged"},
    ]

    data = _get_empty_seats()

    assert data["game"]["opponent"] == "Rams"
    assert data["seats"] == [{
        "seat_id": 1, "section": "112", "row": "10", "seat": "5",
        "price": 85.0, "status": "sth_pinged", "surrender_id": 5,
    }]


def test_empty_seats_seat_with_no_surrender_defaults_to_available(store, fake_supabase):
    store["sth_seat_assignments"] = [
        {"game_id": 1, "seat_id": 1, "seats": {"id": 1, "section": "112", "row": "10", "seat": "5"}},
    ]
    store["games"] = [{"id": 1, "opponent": "Rams", "game_date": "2026-09-13", "arena": "Levi's Stadium"}]
    # No no_shows row, no seat_surrenders row at all.

    data = _get_empty_seats()

    assert data["seats"] == [{
        "seat_id": 1, "section": "112", "row": "10", "seat": "5",
        "price": None, "status": "available", "surrender_id": None,
    }]


# ── GET /api/requests/<id> ───────────────────────────────────────────────────

def _get_request_status(surrender_id):
    with fanxp_api.app.test_request_context(f"/api/requests/{surrender_id}"):
        resp = fanxp_api.request_status(surrender_id)
    return resp.get_json()


def test_non_sold_status_never_includes_a_pass_code(store, fake_supabase):
    store["seat_surrenders"] = [{"id": 1, "status": "sth_pinged"}]

    data = _get_request_status(1)

    assert data == {"surrender_id": 1, "status": "sth_pinged"}
    assert "pass_code" not in data


def test_sold_with_no_auction_at_all_omits_pass_code(store, fake_supabase):
    store["seat_surrenders"] = [{"id": 1, "status": "sold"}]
    # No "auctions" table rows for this surrender at all.

    data = _get_request_status(1)

    assert data["status"] == "sold"
    assert "pass_code" not in data


def test_sold_with_an_auction_but_no_gate_pass_omits_pass_code(store, fake_supabase):
    store["seat_surrenders"] = [{"id": 1, "status": "sold"}]
    store["auctions"] = [{"id": 50, "surrender_id": 1}]
    # No "gate_passes" row for this auction yet.

    data = _get_request_status(1)

    assert data["status"] == "sold"
    assert "pass_code" not in data


def test_sold_with_auction_and_gate_pass_includes_the_pass_code(store, fake_supabase):
    store["seat_surrenders"] = [{"id": 1, "status": "sold"}]
    store["auctions"] = [{"id": 50, "surrender_id": 1}]
    store["gate_passes"] = [{"auction_id": 50, "unique_pass_code": "FANXP-ABC123"}]

    data = _get_request_status(1)

    assert data == {"surrender_id": 1, "status": "sold", "pass_code": "FANXP-ABC123"}


def test_sold_picks_the_most_recent_auction_for_the_surrender(store, fake_supabase):
    # order("id", desc=True).limit(1): if a surrender somehow has more than
    # one auction row, the pass code must come from the newest one.
    store["seat_surrenders"] = [{"id": 1, "status": "sold"}]
    store["auctions"] = [
        {"id": 50, "surrender_id": 1},
        {"id": 60, "surrender_id": 1},
    ]
    store["gate_passes"] = [
        {"auction_id": 50, "unique_pass_code": "FANXP-OLD"},
        {"auction_id": 60, "unique_pass_code": "FANXP-NEW"},
    ]

    data = _get_request_status(1)

    assert data["pass_code"] == "FANXP-NEW"
