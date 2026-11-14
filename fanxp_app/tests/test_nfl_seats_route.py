r"""
Tests for fanxp_accounts.nfl_seats() -- GET /api/nfl/<slug>/seats, the route
behind the live seat map's per-game data feed (CLAUDE.md §4.4: "the seat map
now loads each game's open seats from the API").

test_accounts.py's test_seats_endpoint_merges_no_shows_and_releases and
test_open_seats_for.py already cover the happy path and the seat-merging
logic inside open_seats_for() in detail. This file targets the route's OWN
validation, at fanxp_accounts.py:673-685, which none of those reach:

  if slug not in NFL_SLUGS or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
      return jsonify({"error": "team and date=YYYY-MM-DD required"}), 400

  - An unknown slug returns 400 here, unlike nfl_games() (GET .../games),
    which 404s on an unknown slug (test_nfl_games_route.py). Same "unknown
    team" shape, two different status codes depending on which endpoint you
    hit -- documented as current behavior.
  - A missing/blank `date` query param, and date strings in the wrong shape
    (slashes, two-digit year, no leading zero, trailing/leading junk) all
    400 the same way.
  - The validation is a plain digit-shape ("YYYY-MM-DD") check, not a real
    calendar check -- a syntactically-valid-but-impossible date like
    "2026-13-99" or "2026-02-30" passes validation unchanged and is handed
    straight to open_seats_for(), which (per _games_for()) just won't find
    a game for that date and returns an empty seat list rather than erroring.
  - Surrounding whitespace in `date` is stripped before the regex check, so
    "  2026-10-18  " is accepted exactly like "2026-10-18".
  - A valid request sets the same `Cache-Control: public, max-age=60` header
    as nfl_games().
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

def test_unknown_slug_returns_400_not_404(monkeypatch):
    # nfl_games() 404s on an unknown slug (test_nfl_games_route.py); this
    # route folds the same "unknown team" case into its generic 400.
    setup(monkeypatch)
    code, body = call("/api/nfl/not_a_real_team/seats?date=2026-10-18")
    assert code == 400
    assert "team and date" in body["error"]


# ── Missing/blank date ────────────────────────────────────────────────────────

def test_missing_date_param_returns_400(monkeypatch):
    setup(monkeypatch)
    code, _ = call("/api/nfl/giants/seats")
    assert code == 400


def test_blank_date_param_returns_400(monkeypatch):
    setup(monkeypatch)
    code, _ = call("/api/nfl/giants/seats?date=")
    assert code == 400


# ── Malformed date shapes ─────────────────────────────────────────────────────

def test_slash_separated_date_returns_400(monkeypatch):
    setup(monkeypatch)
    code, _ = call("/api/nfl/giants/seats?date=2026/10/18")
    assert code == 400


def test_two_digit_year_returns_400(monkeypatch):
    setup(monkeypatch)
    code, _ = call("/api/nfl/giants/seats?date=26-10-18")
    assert code == 400


def test_missing_leading_zero_returns_400(monkeypatch):
    setup(monkeypatch)
    code, _ = call("/api/nfl/giants/seats?date=2026-9-5")
    assert code == 400


def test_trailing_junk_after_valid_date_returns_400(monkeypatch):
    setup(monkeypatch)
    code, _ = call("/api/nfl/giants/seats?date=2026-10-18x")
    assert code == 400


# ── Whitespace is stripped before the shape check ─────────────────────────────

def test_date_with_surrounding_whitespace_is_accepted(monkeypatch):
    setup(monkeypatch)
    db = setup(monkeypatch)
    code, body = call("/api/nfl/giants/seats?date=%20%202026-10-18%20%20")
    assert code == 200
    assert body["date"] == "2026-10-18"


# ── Shape check isn't a calendar check ────────────────────────────────────────

def test_calendar_impossible_but_well_shaped_date_is_accepted_and_finds_nothing(monkeypatch):
    # "2026-13-99" matches \d{4}-\d{2}-\d{2} even though month 13 and day 99
    # don't exist. The route doesn't reject it -- it's handed straight to
    # open_seats_for(), which just finds no game for that date and returns
    # an empty seat list rather than raising.
    db = setup(monkeypatch)
    db.tables["games"] = [game(1, "2026-10-18", "New Orleans Saints")]

    code, body = call("/api/nfl/giants/seats?date=2026-13-99")

    assert code == 200
    assert body == {"date": "2026-13-99", "seats": []}


# ── Response shape on a valid request ─────────────────────────────────────────

def test_valid_request_sets_cache_control_header(monkeypatch):
    setup(monkeypatch)
    client = fanxp_api.app.test_client()
    resp = client.get("/api/nfl/giants/seats?date=2026-10-18")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "public, max-age=60"
