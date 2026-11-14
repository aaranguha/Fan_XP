"""
Tests for fanxp_accounts.nfl_seats() -- GET /api/nfl/<slug>/seats, the route
behind the live seat-map's per-game seat feed (CLAUDE.md §4.4: "the seat map
now loads each game's open seats from the API... A game picker switches
games; today's game polls every 60s").

test_accounts.py's test_seats_endpoint_merges_no_shows_and_releases already
covers the happy path end to end (valid team, valid date, real seed data).
test_open_seats_for.py already covers open_seats_for()'s own merge logic in
exhaustive detail. Neither reaches the route's own input validation or its
response shaping, which is what this file targets -- monkeypatching
open_seats_for() itself so these tests are isolated from its internals:

  - Unknown team slug -> 400 (NOT 404, unlike the sibling /games route --
    both bad-team and bad-date share the same "team and date=YYYY-MM-DD
    required" message and status here).
  - Missing `date` query param -> 400.
  - A `date` that doesn't match YYYY-MM-DD (wrong separator, truncated,
    non-numeric, trailing/leading junk, or empty-but-present) -> 400, since
    the route validates with `re.fullmatch`, not `re.match` or `re.search`.
  - A valid team+date calls open_seats_for() with exactly that date and
    shapes its dict into the documented compact
    [section, row, seat, price, source] arrays, one per seat.
  - The Cache-Control header is set on a successful response.
"""

import pytest

import fanxp_accounts
import fanxp_api


def call(path):
    client = fanxp_api.app.test_client()
    resp = client.get(path)
    return resp.status_code, resp.get_json()


def stub_open_seats_for(monkeypatch, result=None, record=None):
    def fake(sb, slug, date):
        if record is not None:
            record.append((slug, date))
        return result or {}
    monkeypatch.setattr(fanxp_accounts, "open_seats_for", fake)
    monkeypatch.setattr(fanxp_accounts, "get_supabase", lambda: object())


# ── Unknown team ──────────────────────────────────────────────────────────

def test_unknown_team_returns_400_not_404(monkeypatch):
    stub_open_seats_for(monkeypatch)
    code, body = call("/api/nfl/not_a_real_team/seats?date=2026-10-18")
    assert code == 400
    assert body == {"error": "team and date=YYYY-MM-DD required"}


# ── Missing/blank date ────────────────────────────────────────────────────

def test_missing_date_param_returns_400():
    code, body = call("/api/nfl/giants/seats")
    assert code == 400
    assert body == {"error": "team and date=YYYY-MM-DD required"}


def test_blank_date_param_returns_400():
    code, body = call("/api/nfl/giants/seats?date=")
    assert code == 400


def test_whitespace_only_date_param_returns_400():
    code, body = call("/api/nfl/giants/seats?date=%20%20%20")
    assert code == 400


# ── Malformed date (re.fullmatch, not match/search) ──────────────────────

@pytest.mark.parametrize("bad_date", [
    "2026/10/18",       # wrong separator
    "26-10-18",         # 2-digit year
    "2026-10-1",         # single-digit day
    "2026-1-18",          # single-digit month
    "2026-10-188",       # trailing extra digit
    "x2026-10-18",       # leading junk -- fullmatch must reject, match() wouldn't
    "2026-10-18x",       # trailing junk -- fullmatch must reject, search() wouldn't
    "not-a-date",
    "20261018",
])
def test_malformed_date_returns_400(bad_date):
    code, body = call(f"/api/nfl/giants/seats?date={bad_date}")
    assert code == 400
    assert body == {"error": "team and date=YYYY-MM-DD required"}


def test_well_formed_date_is_accepted(monkeypatch):
    stub_open_seats_for(monkeypatch)
    code, _ = call("/api/nfl/giants/seats?date=2026-10-18")
    assert code == 200


# ── Valid request: delegates to open_seats_for() and shapes its result ──────

def test_valid_request_passes_through_slug_and_date(monkeypatch):
    seen = []
    stub_open_seats_for(monkeypatch, record=seen)
    call("/api/nfl/giants/seats?date=2026-10-18")
    assert seen == [("giants", "2026-10-18")]


def test_response_shape_compacts_seat_dict_into_arrays(monkeypatch):
    stub_open_seats_for(monkeypatch, result={
        ("115", "B", "5"): {"price": 120, "source": "no_show"},
        ("203", "C", "9"): {"price": 42, "source": "release", "release_id": 7},
    })

    code, body = call("/api/nfl/giants/seats?date=2026-10-18")

    assert code == 200
    assert body["date"] == "2026-10-18"
    seats = {(s[0], s[1], s[2]): (s[3], s[4]) for s in body["seats"]}
    assert seats == {
        ("115", "B", "5"): (120, "no_show"),
        ("203", "C", "9"): (42, "release"),
    }


def test_no_open_seats_returns_empty_seats_list(monkeypatch):
    stub_open_seats_for(monkeypatch, result={})
    code, body = call("/api/nfl/giants/seats?date=2026-10-18")
    assert code == 200
    assert body["seats"] == []


def test_price_none_is_preserved_through_the_array_shape(monkeypatch):
    stub_open_seats_for(monkeypatch, result={
        ("115", "B", "5"): {"price": None, "source": "no_show"},
    })
    _, body = call("/api/nfl/giants/seats?date=2026-10-18")
    assert body["seats"] == [["115", "B", "5", None, "no_show"]]


# ── Caching ───────────────────────────────────────────────────────────────

def test_cache_control_header_is_set_on_success(monkeypatch):
    stub_open_seats_for(monkeypatch)
    client = fanxp_api.app.test_client()
    resp = client.get("/api/nfl/giants/seats?date=2026-10-18")
    assert resp.headers["Cache-Control"] == "public, max-age=60"
