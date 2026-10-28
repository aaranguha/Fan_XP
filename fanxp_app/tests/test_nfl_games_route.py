"""
Tests for fanxp_accounts.nfl_games() -- GET /api/nfl/<slug>/games, the route
that lists a team's games for the seat-map's game picker (CLAUDE.md §4.4).

test_accounts.py's test_games_endpoint_lists_game_with_counts already covers
one happy path (a single past game with no-shows and a release). This file
targets the route's own filtering/merging logic, which that smoke test never
exercises:

  - The bad-row filter for scraped add-on-page listings. CLAUDE.md §9 records
    a real incident still live in production data: "Bad 9/13 rows from the
    add-on-page bug (Steelers club pass, game 1235; Raiders hotel package,
    1243) are still in Supabase; the API hides them." That hiding is this
    exact `any(m in (gm.get("opponent") or "").lower() for m in (...))`
    check in nfl_games() -- untested until now.
  - A past game with zero no-shows is dropped (stale/incomplete data), but a
    *future* game with zero no-shows is kept (an upcoming game to pick).
  - A past game that DOES have no-shows is kept despite being in the past
    (so historical games stay visible/claimable in the UI).
  - Releases merge into an existing entry's `releases` count, but can also
    resurrect a date into the output via `setdefault` even when that date's
    own `games` row was filtered out by the bad-opponent check above --
    documented as current (if surprising) behavior, not asserted as
    correct.
  - The `live` flag is only true for today's date.
  - Unknown slug -> 404.

Uses the same in-memory fake Supabase shape as test_accounts.py, extended
with count="exact" and gte() support, which that harness doesn't need but
this route does.
"""

from types import SimpleNamespace

import fanxp_accounts
import fanxp_api


class Q:
    def __init__(self, db, table):
        self.db, self.table, self.f, self._count = db, table, [], False

    def _rows(self):
        return self.db.tables.setdefault(self.table, [])

    def select(self, cols="*", count=None):
        self._count = count == "exact"
        return self

    def eq(self, k, v):
        self.f.append((k, lambda x, v=v: str(x) == str(v)))
        return self

    def gte(self, k, v):
        self.f.append((k, lambda x, v=v: str(x) >= str(v)))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def _get(self, r, k):
        if "." in k:
            a, b = k.split(".", 1)
            return (r.get(a) or {}).get(b)
        return r.get(k)

    def _join(self, r):
        if self.table == "seat_releases":
            seat = next((s for s in self.db.tables.get("seller_seats", []) if s["id"] == r["seller_seat_id"]), {})
            return {**r, "seller_seats": seat}
        return r

    def execute(self):
        rows = [self._join(r) for r in self._rows()]
        matched = [r for r in rows if all(fn(self._get(r, k)) for k, fn in self.f)]
        return SimpleNamespace(data=matched, count=len(matched) if self._count else None)


class FakeDB:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return Q(self, name)


def setup(monkeypatch, today="2026-10-20"):
    db = FakeDB()
    monkeypatch.setattr(fanxp_accounts, "get_supabase", lambda: db)
    monkeypatch.setattr(fanxp_accounts, "today_et", lambda: today)
    return db


def call(path):
    client = fanxp_api.app.test_client()
    resp = client.get(path)
    return resp.status_code, resp.get_json()


def game(id, date, opponent):
    return {"id": id, "league": "nfl", "home_team": "giants", "game_date": date, "opponent": opponent}


# ── Unknown slug ─────────────────────────────────────────────────────────────

def test_unknown_slug_returns_404(monkeypatch):
    setup(monkeypatch)
    code, body = call("/api/nfl/not_a_real_team/games")
    assert code == 404


# ── Bad-row filter (CLAUDE.md §9: Steelers club pass / Raiders hotel package) ─

def test_club_pass_add_on_row_is_hidden_even_with_no_shows(monkeypatch):
    db = setup(monkeypatch)
    db.tables["games"] = [game(1235, "2026-09-13", "Steelers Club Pass")]
    db.tables["no_shows"] = [{"id": 1, "game_id": 1235}]

    code, body = call("/api/nfl/giants/games")

    assert code == 200
    assert body == []


def test_hotel_package_add_on_row_is_hidden(monkeypatch):
    db = setup(monkeypatch)
    db.tables["games"] = [game(1243, "2026-09-13", "Raiders Hotel Package")]

    code, body = call("/api/nfl/giants/games")

    assert body == []


def test_not_a_game_ticket_row_is_hidden(monkeypatch):
    db = setup(monkeypatch)
    db.tables["games"] = [game(99, "2026-09-20", "Denver Broncos - Not A Game Ticket")]

    code, body = call("/api/nfl/giants/games")

    assert body == []


def test_bad_row_filter_is_case_insensitive(monkeypatch):
    db = setup(monkeypatch)
    db.tables["games"] = [game(100, "2026-09-20", "CLUB PASS Upgrade")]

    _, body = call("/api/nfl/giants/games")

    assert body == []


def test_real_matchup_mentioning_none_of_the_noise_terms_is_kept(monkeypatch):
    db = setup(monkeypatch)
    db.tables["games"] = [game(1, "2026-10-25", "Dallas Cowboys")]

    code, body = call("/api/nfl/giants/games")

    assert code == 200
    assert [g["date"] for g in body] == ["2026-10-25"]


# ── Past vs. future with/without no-shows ────────────────────────────────────

def test_past_game_with_zero_no_shows_is_dropped(monkeypatch):
    db = setup(monkeypatch, today="2026-10-20")
    db.tables["games"] = [game(1, "2026-10-11", "Dallas Cowboys")]  # past, no no_shows row at all

    _, body = call("/api/nfl/giants/games")

    assert body == []


def test_past_game_with_no_shows_is_kept_despite_being_in_the_past(monkeypatch):
    db = setup(monkeypatch, today="2026-10-20")
    db.tables["games"] = [game(1, "2026-10-11", "Dallas Cowboys")]
    db.tables["no_shows"] = [{"id": 1, "game_id": 1}, {"id": 2, "game_id": 1}]

    _, body = call("/api/nfl/giants/games")

    assert len(body) == 1
    assert body[0]["date"] == "2026-10-11"
    assert body[0]["no_shows"] == 2


def test_future_game_with_zero_no_shows_is_kept_as_upcoming(monkeypatch):
    db = setup(monkeypatch, today="2026-10-20")
    db.tables["games"] = [game(1, "2026-10-25", "Dallas Cowboys")]  # future, not yet played

    _, body = call("/api/nfl/giants/games")

    assert len(body) == 1
    assert body[0]["no_shows"] == 0


def test_todays_game_counts_as_not_in_the_past(monkeypatch):
    db = setup(monkeypatch, today="2026-10-20")
    db.tables["games"] = [game(1, "2026-10-20", "Dallas Cowboys")]

    _, body = call("/api/nfl/giants/games")

    assert len(body) == 1
    assert body[0]["live"] is True


def test_live_flag_is_false_for_a_non_today_game(monkeypatch):
    db = setup(monkeypatch, today="2026-10-20")
    db.tables["games"] = [game(1, "2026-10-25", "Dallas Cowboys")]

    _, body = call("/api/nfl/giants/games")

    assert body[0]["live"] is False


# ── Releases merging ─────────────────────────────────────────────────────────

def test_release_increments_the_releases_count_on_an_existing_entry(monkeypatch):
    db = setup(monkeypatch, today="2026-10-20")
    db.tables["games"] = [game(1, "2026-10-25", "Dallas Cowboys")]
    db.tables["seller_seats"] = [{"id": 1, "team_slug": "giants", "section": "115",
                                  "row_label": "B", "seat_num": "6"}]
    db.tables["seat_releases"] = [{"id": 1, "seller_seat_id": 1, "game_date": "2026-10-25",
                                   "status": "available"}]

    _, body = call("/api/nfl/giants/games")

    assert len(body) == 1
    assert body[0]["releases"] == 1
    assert body[0]["no_shows"] == 0


def test_release_for_a_date_hidden_by_the_bad_row_filter_resurrects_it_with_blank_opponent(monkeypatch):
    # Documents current behavior, not asserted as correct: the games-table
    # loop `continue`s past a bad-opponent row and never adds its date to
    # `out`, but the releases loop re-adds that same date via setdefault()
    # with opponent="" -- a seller release for the same date as a hidden
    # add-on-page row would make that date reappear on the picker anyway.
    # (The releases query is itself scoped to `game_date >= today`, so this
    # only happens for an upcoming date, not a past one.)
    db = setup(monkeypatch, today="2026-10-20")
    db.tables["games"] = [game(1235, "2026-10-25", "Steelers Club Pass")]
    db.tables["seller_seats"] = [{"id": 1, "team_slug": "giants", "section": "115",
                                  "row_label": "B", "seat_num": "6"}]
    db.tables["seat_releases"] = [{"id": 1, "seller_seat_id": 1, "game_date": "2026-10-25",
                                   "status": "available"}]

    _, body = call("/api/nfl/giants/games")

    assert [g["date"] for g in body] == ["2026-10-25"]
    assert body[0]["opponent"] == ""
    assert body[0]["releases"] == 1


def test_only_available_releases_in_the_future_are_counted(monkeypatch):
    db = setup(monkeypatch, today="2026-10-20")
    db.tables["games"] = [game(1, "2026-10-25", "Dallas Cowboys")]
    db.tables["seller_seats"] = [{"id": 1, "team_slug": "giants", "section": "115",
                                  "row_label": "B", "seat_num": "6"}]
    db.tables["seat_releases"] = [
        {"id": 1, "seller_seat_id": 1, "game_date": "2026-10-25", "status": "claimed"},     # not available
        {"id": 2, "seller_seat_id": 1, "game_date": "2026-10-01", "status": "available"},   # in the past
    ]

    _, body = call("/api/nfl/giants/games")

    assert len(body) == 1
    assert body[0]["releases"] == 0


# ── Response shape ────────────────────────────────────────────────────────────

def test_results_are_sorted_most_recent_date_first(monkeypatch):
    db = setup(monkeypatch, today="2026-10-20")
    db.tables["games"] = [
        game(1, "2026-10-11", "Dallas Cowboys"),
        game(2, "2026-10-25", "New York Jets"),
        game(3, "2026-10-18", "Buffalo Bills"),
    ]
    db.tables["no_shows"] = [{"id": i, "game_id": gid} for i, gid in enumerate([1, 3], start=1)]

    _, body = call("/api/nfl/giants/games")

    assert [g["date"] for g in body] == ["2026-10-25", "2026-10-18", "2026-10-11"]


def test_cache_control_header_is_set(monkeypatch):
    db = setup(monkeypatch)
    db.tables["games"] = [game(1, "2026-10-25", "Dallas Cowboys")]

    client = fanxp_api.app.test_client()
    resp = client.get("/api/nfl/giants/games")

    assert resp.headers["Cache-Control"] == "public, max-age=60"
