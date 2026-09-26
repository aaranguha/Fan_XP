"""
Tests for generate_capability_statement.py's collect_proof_of_concept_stats()
and its MAX_SANE_NO_SHOWS filter.

This is the sales collateral (capability_statement.html) shown directly to
prospective teams/investors, so a bad number here isn't just a bug -- it's a
false claim made to a customer. The module's own docstring explains why the
filter exists: MLB's no_shows table has a known duplicate-insert bug where
some games show 100,000+ "no-shows" (physically impossible for any stadium),
so MAX_SANE_NO_SHOWS=500 excludes any game above that bound from every total,
and the "proof of concept" section only ever queries NBA/WNBA in the first
place, never MLB. These tests drive collect_proof_of_concept_stats() against
a fake Supabase client -- no real network access or credentials needed.
"""

import generate_capability_statement as gcs


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

    def select(self, *_args, count=None):
        self._count_mode = count
        return self

    def eq(self, field, value):
        self.filters[field] = value
        return self

    def execute(self):
        rows = self.store.get(self.table_name, [])
        matching = [r for r in rows if all(r.get(k) == v for k, v in self.filters.items())]
        count = len(matching) if self._count_mode == "exact" else None
        return FakeResult(data=matching, count=count)


class FakeClient:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return FakeQuery(name, self.store)


def make_store(games, no_shows):
    return {"games": games, "no_shows": no_shows}


def _game(game_id, league):
    return {"id": game_id, "league": league}


def _no_show_rows(game_id, n, price=25.0):
    return [{"game_id": game_id, "price_usd": price} for _ in range(n)]


def test_no_client_configured_returns_zeroed_stats(monkeypatch):
    monkeypatch.setattr(gcs.supabase_client, "_get_client", lambda: None)

    assert gcs.collect_proof_of_concept_stats() == {"games": 0, "no_shows": 0, "value": 0.0}


def test_clean_nba_and_wnba_games_are_counted_and_summed(monkeypatch):
    games = [_game(1, "nba"), _game(2, "wnba")]
    no_shows = _no_show_rows(1, 10, price=20.0) + _no_show_rows(2, 5, price=30.0)

    client = FakeClient(make_store(games, no_shows))
    monkeypatch.setattr(gcs.supabase_client, "_get_client", lambda: client)

    stats = gcs.collect_proof_of_concept_stats()

    assert stats["games"] == 2
    assert stats["no_shows"] == 15
    assert stats["value"] == 10 * 20.0 + 5 * 30.0


def test_mlb_games_are_never_queried_even_if_present(monkeypatch):
    # The docstring's whole point: MLB's no_shows data is unreliable, so the
    # proof-of-concept loop only ever iterates ("nba", "wnba") -- an MLB game
    # sitting in the same tables must not leak into the totals at all, filter
    # or no filter.
    games = [_game(1, "mlb"), _game(2, "nba")]
    no_shows = _no_show_rows(1, 100000) + _no_show_rows(2, 8)

    client = FakeClient(make_store(games, no_shows))
    monkeypatch.setattr(gcs.supabase_client, "_get_client", lambda: client)

    stats = gcs.collect_proof_of_concept_stats()

    assert stats["games"] == 1
    assert stats["no_shows"] == 8


def test_game_above_max_sane_no_shows_is_excluded(monkeypatch):
    # Regression guard for the real corruption incident: a game with an
    # impossible no_shows count must not inflate the totals shown to a team.
    games = [_game(1, "nba"), _game(2, "nba")]
    no_shows = _no_show_rows(1, gcs.MAX_SANE_NO_SHOWS + 1) + _no_show_rows(2, 12)

    client = FakeClient(make_store(games, no_shows))
    monkeypatch.setattr(gcs.supabase_client, "_get_client", lambda: client)

    stats = gcs.collect_proof_of_concept_stats()

    assert stats["games"] == 1
    assert stats["no_shows"] == 12


def test_boundary_at_and_above_max_sane_no_shows(monkeypatch):
    # The filter is "<= MAX_SANE_NO_SHOWS", so the boundary value itself is
    # legitimate data, not corruption -- must not be an off-by-one "<".
    games = [_game(1, "nba"), _game(2, "nba")]
    no_shows = _no_show_rows(1, gcs.MAX_SANE_NO_SHOWS) + _no_show_rows(2, gcs.MAX_SANE_NO_SHOWS + 1)

    client = FakeClient(make_store(games, no_shows))
    monkeypatch.setattr(gcs.supabase_client, "_get_client", lambda: client)

    stats = gcs.collect_proof_of_concept_stats()

    # Game 1 (exactly at the ceiling) counts; game 2 (one over) is excluded.
    assert stats["games"] == 1
    assert stats["no_shows"] == gcs.MAX_SANE_NO_SHOWS


def test_game_with_zero_no_shows_is_excluded(monkeypatch):
    # r.count must be > 0 to count -- a game with zero recorded no-shows
    # shouldn't be cited as a "game tracked" in the proof-of-concept total.
    games = [_game(1, "nba")]
    no_shows = []

    client = FakeClient(make_store(games, no_shows))
    monkeypatch.setattr(gcs.supabase_client, "_get_client", lambda: client)

    stats = gcs.collect_proof_of_concept_stats()

    assert stats == {"games": 0, "no_shows": 0, "value": 0.0}


def test_null_price_usd_falls_back_to_zero_instead_of_crashing(monkeypatch):
    games = [_game(1, "nba")]
    no_shows = [
        {"game_id": 1, "price_usd": None},
        {"game_id": 1, "price_usd": 15.5},
    ]

    client = FakeClient(make_store(games, no_shows))
    monkeypatch.setattr(gcs.supabase_client, "_get_client", lambda: client)

    stats = gcs.collect_proof_of_concept_stats()

    assert stats["no_shows"] == 2
    assert stats["value"] == 15.5


def test_no_games_at_all_returns_zeroed_stats(monkeypatch):
    client = FakeClient(make_store([], []))
    monkeypatch.setattr(gcs.supabase_client, "_get_client", lambda: client)

    assert gcs.collect_proof_of_concept_stats() == {"games": 0, "no_shows": 0, "value": 0.0}
