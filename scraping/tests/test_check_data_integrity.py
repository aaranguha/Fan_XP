"""
Tests for check_data_integrity.py's impossible-no_shows invariant.

A no_show is defined as a seat present in BOTH the pre_game and halftime
snapshots, so a game's no_shows count can never exceed min(pre_game_count,
halftime_count). This isn't a heuristic — it's mathematically impossible for
a genuine intersection, so any row that violates it is corrupted data.

Confirmed root cause (see module docstring in check_data_integrity.py): a
2026-09-20 git-push failure let two mismatched snapshots from different runs
get compared against each other, inserting no_shows counts far exceeding
either snapshot (one real game hit 105,192 "no-show" rows — physically
impossible for any stadium). These tests drive main() end-to-end against a
fake Supabase client so no real network access or credentials are needed.
"""

import sys

import pytest

import check_data_integrity as cdi


class FakeResult:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class FakeQuery:
    def __init__(self, table_name, store):
        self.table_name = table_name
        self.store = store
        self.filters = {}
        self._count_mode = None
        self._gt = None
        self._order = None
        self._limit = None
        self._delete = False
        self._in_field = None
        self._in_values = None

    def select(self, *_args, count=None):
        self._count_mode = count
        return self

    def eq(self, field, value):
        self.filters[field] = value
        return self

    def gt(self, field, value):
        self._gt = (field, value)
        return self

    def order(self, _field):
        self._order = _field
        return self

    def limit(self, n):
        self._limit = n
        return self

    def delete(self):
        self._delete = True
        return self

    def in_(self, field, values):
        self._in_field = field
        self._in_values = list(values)
        return self

    def _matching(self):
        rows = self.store.get(self.table_name, [])
        out = [r for r in rows if all(r.get(k) == v for k, v in self.filters.items())]
        if self._gt:
            field, val = self._gt
            out = [r for r in out if r.get(field, -1) > val]
        if self._in_field is not None:
            wanted = set(self._in_values)
            out = [r for r in out if r.get(self._in_field) in wanted]
        if self._order:
            out = sorted(out, key=lambda r: r[self._order])
        return out

    def execute(self):
        matching = self._matching()

        if self._delete:
            delete_ids = {r["id"] for r in matching}
            self.store[self.table_name] = [
                r for r in self.store.get(self.table_name, []) if r["id"] not in delete_ids
            ]
            return FakeResult(data=[], count=None)

        total = len(matching)
        limited = matching[: self._limit] if self._limit is not None else matching
        count = total if self._count_mode == "exact" else None
        return FakeResult(data=limited, count=count)


class FakeClient:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return FakeQuery(name, self.store)


def make_store(games, listings, no_shows):
    return {"games": games, "listings": listings, "no_shows": no_shows}


def _listing_rows(game_id, league, snapshot, n):
    return [
        {"id": f"{game_id}-{snapshot}-{i}", "game_id": game_id, "league": league, "snapshot": snapshot}
        for i in range(n)
    ]


def _no_show_rows(game_id, league, n, start_id=0):
    return [{"id": start_id + i, "game_id": game_id, "league": league} for i in range(n)]


@pytest.fixture(autouse=True)
def restore_argv():
    original = sys.argv[:]
    yield
    sys.argv = original


# ── The core invariant ────────────────────────────────────────────

def test_plausible_no_shows_are_not_flagged(monkeypatch, capsys):
    games = [{"id": 1, "league": "nfl", "home_team": "49ers", "opponent": "rams", "game_date": "2026-09-13"}]
    listings = _listing_rows(1, "nfl", "pre_game", 100) + _listing_rows(1, "nfl", "halftime", 80)
    no_shows = _no_show_rows(1, "nfl", 50)

    client = FakeClient(make_store(games, listings, no_shows))
    monkeypatch.setattr(cdi, "_get_client", lambda: client)
    monkeypatch.setattr(sys, "argv", ["check_data_integrity.py", "--dry-run"])

    assert cdi.main() == 0
    assert "Clean" in capsys.readouterr().out


def test_no_shows_exceeding_min_snapshot_is_flagged(monkeypatch, capsys):
    # The confirmed incident: no_shows blew past BOTH raw snapshot counts.
    games = [{"id": 1, "league": "nfl", "home_team": "49ers", "opponent": "rams", "game_date": "2026-09-13"}]
    listings = _listing_rows(1, "nfl", "pre_game", 100) + _listing_rows(1, "nfl", "halftime", 80)
    no_shows = _no_show_rows(1, "nfl", 150)

    client = FakeClient(make_store(games, listings, no_shows))
    monkeypatch.setattr(cdi, "_get_client", lambda: client)
    monkeypatch.setattr(sys, "argv", ["check_data_integrity.py", "--dry-run"])

    assert cdi.main() == 1
    out = capsys.readouterr().out
    assert "impossible no_shows" in out
    assert "max possible: 80" in out


def test_no_shows_exactly_at_the_ceiling_is_not_flagged(monkeypatch):
    # no_shows == min(pre, half) is the maximum legitimate value, not a
    # violation -- the check must be a strict ">", not ">=".
    games = [{"id": 1, "league": "nfl", "home_team": "49ers", "opponent": "rams", "game_date": "2026-09-13"}]
    listings = _listing_rows(1, "nfl", "pre_game", 100) + _listing_rows(1, "nfl", "halftime", 80)
    no_shows = _no_show_rows(1, "nfl", 80)

    client = FakeClient(make_store(games, listings, no_shows))
    monkeypatch.setattr(cdi, "_get_client", lambda: client)
    monkeypatch.setattr(sys, "argv", ["check_data_integrity.py", "--dry-run"])

    assert cdi.main() == 0


def test_game_missing_a_snapshot_is_skipped_not_flagged(monkeypatch):
    # Can't evaluate the invariant without both snapshots -- a game that's
    # only had its pre_game scrape so far must not be reported as corrupted.
    games = [{"id": 1, "league": "nfl", "home_team": "49ers", "opponent": "rams", "game_date": "2026-09-13"}]
    listings = _listing_rows(1, "nfl", "pre_game", 100)  # no halftime rows at all
    no_shows = []

    client = FakeClient(make_store(games, listings, no_shows))
    monkeypatch.setattr(cdi, "_get_client", lambda: client)
    monkeypatch.setattr(sys, "argv", ["check_data_integrity.py", "--dry-run"])

    assert cdi.main() == 0


def test_only_the_corrupted_game_is_reported(monkeypatch, capsys):
    games = [
        {"id": 1, "league": "nfl", "home_team": "49ers", "opponent": "rams", "game_date": "2026-09-13"},
        {"id": 2, "league": "nfl", "home_team": "seahawks", "opponent": "cardinals", "game_date": "2026-09-14"},
    ]
    listings = (
        _listing_rows(1, "nfl", "pre_game", 100) + _listing_rows(1, "nfl", "halftime", 80)
        + _listing_rows(2, "nfl", "pre_game", 50) + _listing_rows(2, "nfl", "halftime", 40)
    )
    no_shows = _no_show_rows(1, "nfl", 30) + _no_show_rows(2, "nfl", 999, start_id=1000)

    client = FakeClient(make_store(games, listings, no_shows))
    monkeypatch.setattr(cdi, "_get_client", lambda: client)
    monkeypatch.setattr(sys, "argv", ["check_data_integrity.py", "--dry-run"])

    assert cdi.main() == 1
    out = capsys.readouterr().out
    assert "Found 1 game" in out
    assert "game 2" in out
    assert "game 1" not in out


def test_league_filter_only_checks_the_requested_league(monkeypatch):
    # An MLB game with corrupted counts must not surface when --league nfl
    # is requested (leagues share the same tables, filtered by 'league').
    games = [{"id": 1, "league": "mlb", "home_team": "dbacks", "opponent": "giants", "game_date": "2026-06-01"}]
    listings = _listing_rows(1, "mlb", "pre_game", 100) + _listing_rows(1, "mlb", "halftime", 80)
    no_shows = _no_show_rows(1, "mlb", 100000)

    client = FakeClient(make_store(games, listings, no_shows))
    monkeypatch.setattr(cdi, "_get_client", lambda: client)
    monkeypatch.setattr(sys, "argv", ["check_data_integrity.py", "--league", "nfl", "--dry-run"])

    # games table itself is filtered by league too, so an mlb-only games
    # list means zero nfl games get evaluated at all.
    assert cdi.main() == 0


# ── Cleanup behavior ────────────────────────────────────────────

def test_dry_run_reports_but_does_not_delete(monkeypatch):
    games = [{"id": 1, "league": "nfl", "home_team": "49ers", "opponent": "rams", "game_date": "2026-09-13"}]
    listings = _listing_rows(1, "nfl", "pre_game", 100) + _listing_rows(1, "nfl", "halftime", 80)
    no_shows = _no_show_rows(1, "nfl", 150)

    store = make_store(games, listings, no_shows)
    client = FakeClient(store)
    monkeypatch.setattr(cdi, "_get_client", lambda: client)
    monkeypatch.setattr(sys, "argv", ["check_data_integrity.py", "--dry-run"])

    cdi.main()

    assert len(store["no_shows"]) == 150


def test_without_dry_run_corrupted_rows_are_deleted(monkeypatch):
    games = [{"id": 1, "league": "nfl", "home_team": "49ers", "opponent": "rams", "game_date": "2026-09-13"}]
    listings = _listing_rows(1, "nfl", "pre_game", 100) + _listing_rows(1, "nfl", "halftime", 80)
    no_shows = _no_show_rows(1, "nfl", 150)

    store = make_store(games, listings, no_shows)
    client = FakeClient(store)
    monkeypatch.setattr(cdi, "_get_client", lambda: client)
    monkeypatch.setattr(sys, "argv", ["check_data_integrity.py"])

    assert cdi.main() == 1
    assert store["no_shows"] == []


def test_delete_batches_larger_than_200_ids_still_delete_everything(monkeypatch):
    # Regression guard for the documented PostgREST request-size limit: a
    # single .in_() call with thousands of ids fails, so deletes are chunked
    # at 200. A broken chunk loop (off-by-one, wrong slice) would silently
    # leave some corrupted rows behind instead of erroring loudly.
    games = [{"id": 1, "league": "nfl", "home_team": "49ers", "opponent": "rams", "game_date": "2026-09-13"}]
    listings = _listing_rows(1, "nfl", "pre_game", 500) + _listing_rows(1, "nfl", "halftime", 500)
    no_shows = _no_show_rows(1, "nfl", 700)  # > min(pre, half) and > one batch

    store = make_store(games, listings, no_shows)
    client = FakeClient(store)
    monkeypatch.setattr(cdi, "_get_client", lambda: client)
    monkeypatch.setattr(sys, "argv", ["check_data_integrity.py"])

    assert cdi.main() == 1
    assert store["no_shows"] == []


def test_other_games_no_shows_are_left_untouched(monkeypatch):
    games = [
        {"id": 1, "league": "nfl", "home_team": "49ers", "opponent": "rams", "game_date": "2026-09-13"},
        {"id": 2, "league": "nfl", "home_team": "seahawks", "opponent": "cardinals", "game_date": "2026-09-14"},
    ]
    listings = (
        _listing_rows(1, "nfl", "pre_game", 100) + _listing_rows(1, "nfl", "halftime", 80)
        + _listing_rows(2, "nfl", "pre_game", 50) + _listing_rows(2, "nfl", "halftime", 40)
    )
    no_shows = _no_show_rows(1, "nfl", 150) + _no_show_rows(2, "nfl", 20, start_id=1000)

    store = make_store(games, listings, no_shows)
    client = FakeClient(store)
    monkeypatch.setattr(cdi, "_get_client", lambda: client)
    monkeypatch.setattr(sys, "argv", ["check_data_integrity.py"])

    cdi.main()

    remaining_game_ids = {r["game_id"] for r in store["no_shows"]}
    assert remaining_game_ids == {2}
    assert len(store["no_shows"]) == 20


# ── Supabase not configured ────────────────────────────────────

def test_returns_cleanly_when_supabase_not_configured(monkeypatch, capsys):
    monkeypatch.setattr(cdi, "_get_client", lambda: None)
    monkeypatch.setattr(sys, "argv", ["check_data_integrity.py"])

    assert cdi.main() == 0
    assert "not configured" in capsys.readouterr().out
