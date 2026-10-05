"""
Tests for daily_runner.py's schedule-fetching/home-team-resolution logic —
the NBA daily entry point's analog to nfl_runner.get_home_teams_tm() and
mlb_runner/wnba_runner's get_home_teams_tm(): it decides which teams get a
runner subprocess launched for them today, so a bug here either silently
drops a real home game or (worse) launches a runner for a team that isn't
actually playing at home today.

Covers three functions, driven through monkeypatched requests.get so no
real network access is needed:

  - get_home_teams_espn()    primary schedule source (ESPN scoreboard API)
  - get_home_teams_nba_cdn() fallback schedule source (NBA CDN static JSON)
  - get_home_teams_today()   ESPN-first-then-CDN-fallback orchestration

NBA is currently paused (see CLAUDE.md §2) but this logic is unchanged from
when it last ran and the same class of schedule-source bug documented for
NFL/MLB/WNBA's runners (unmapped tricodes, missing home/away sides) applies
here too.
"""

import pytest

import daily_runner


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FailingResponse:
    def raise_for_status(self):
        raise RuntimeError("HTTP 500")

    def json(self):
        raise AssertionError("json() should not be called when raise_for_status() raises")


def _espn_payload(*matchups):
    """Each matchup is (home_tricode, home_name, away_name)."""
    events = []
    for home_tricode, home_name, away_name in matchups:
        events.append({
            "competitions": [{
                "competitors": [
                    {"homeAway": "home", "team": {"abbreviation": home_tricode, "displayName": home_name}},
                    {"homeAway": "away", "team": {"abbreviation": "XXX", "displayName": away_name}},
                ]
            }]
        })
    return {"events": events}


def _cdn_payload(date_str, *matchups):
    """Each matchup is (home_tricode, home_city, away_city)."""
    games = [
        {
            "homeTeam": {"teamTricode": home_tricode, "teamCity": home_city},
            "awayTeam": {"teamCity": away_city},
        }
        for home_tricode, home_city, away_city in matchups
    ]
    return {"leagueSchedule": {"gameDates": [{"gameDate": date_str, "games": games}]}}


# ── get_home_teams_espn() ────────────────────────────────────────────────

def test_espn_finds_home_team_for_plain_matchup(monkeypatch):
    monkeypatch.setattr(
        daily_runner.requests, "get",
        lambda *a, **k: FakeResponse(_espn_payload(("GSW", "Golden State Warriors", "Los Angeles Lakers"))),
    )

    assert daily_runner.get_home_teams_espn("2026-11-05") == ["warriors"]


def test_espn_multiple_games_all_returned_in_order(monkeypatch):
    monkeypatch.setattr(
        daily_runner.requests, "get",
        lambda *a, **k: FakeResponse(_espn_payload(
            ("BOS", "Boston Celtics", "Miami Heat"),
            ("LAL", "Los Angeles Lakers", "Denver Nuggets"),
        )),
    )

    assert daily_runner.get_home_teams_espn("2026-11-05") == ["celtics", "lakers"]


def test_espn_unmapped_tricode_is_skipped(monkeypatch):
    # An expansion/all-star/G-League tricode not in NBA_TRICODE_TO_SLUG must
    # not crash the whole day's schedule -- it's just silently skipped.
    monkeypatch.setattr(
        daily_runner.requests, "get",
        lambda *a, **k: FakeResponse(_espn_payload(("ZZZ", "All-Star Team", "Boston Celtics"))),
    )

    assert daily_runner.get_home_teams_espn("2026-11-05") == []


def test_espn_event_with_no_home_side_is_skipped(monkeypatch):
    # Malformed/neutral-site event where no competitor is marked "home".
    payload = {
        "events": [{
            "competitions": [{
                "competitors": [
                    {"homeAway": "away", "team": {"abbreviation": "GSW", "displayName": "Golden State Warriors"}},
                ]
            }]
        }]
    }
    monkeypatch.setattr(daily_runner.requests, "get", lambda *a, **k: FakeResponse(payload))

    assert daily_runner.get_home_teams_espn("2026-11-05") == []


def test_espn_http_failure_propagates(monkeypatch):
    monkeypatch.setattr(daily_runner.requests, "get", lambda *a, **k: FailingResponse())

    with pytest.raises(RuntimeError):
        daily_runner.get_home_teams_espn("2026-11-05")


# ── get_home_teams_nba_cdn() ─────────────────────────────────────────────

def test_cdn_finds_home_team_for_matching_date(monkeypatch):
    monkeypatch.setattr(
        daily_runner.requests, "get",
        lambda *a, **k: FakeResponse(_cdn_payload(
            "11/05/2026 00:00:00", ("GSW", "Golden State", "Los Angeles"),
        )),
    )

    assert daily_runner.get_home_teams_nba_cdn("2026-11-05") == ["warriors"]


def test_cdn_ignores_game_dates_that_dont_match_requested_day(monkeypatch):
    payload = {
        "leagueSchedule": {
            "gameDates": [
                {"gameDate": "11/04/2026 00:00:00", "games": [
                    {"homeTeam": {"teamTricode": "BOS", "teamCity": "Boston"}, "awayTeam": {"teamCity": "Miami"}},
                ]},
                {"gameDate": "11/05/2026 00:00:00", "games": [
                    {"homeTeam": {"teamTricode": "GSW", "teamCity": "Golden State"}, "awayTeam": {"teamCity": "LA"}},
                ]},
            ]
        }
    }
    monkeypatch.setattr(daily_runner.requests, "get", lambda *a, **k: FakeResponse(payload))

    assert daily_runner.get_home_teams_nba_cdn("2026-11-05") == ["warriors"]


def test_cdn_unmapped_tricode_is_skipped(monkeypatch):
    monkeypatch.setattr(
        daily_runner.requests, "get",
        lambda *a, **k: FakeResponse(_cdn_payload(
            "11/05/2026 00:00:00", ("ZZZ", "Unknown", "Boston"),
        )),
    )

    assert daily_runner.get_home_teams_nba_cdn("2026-11-05") == []


def test_cdn_no_matching_date_returns_empty(monkeypatch):
    monkeypatch.setattr(
        daily_runner.requests, "get",
        lambda *a, **k: FakeResponse(_cdn_payload(
            "11/04/2026 00:00:00", ("GSW", "Golden State", "LA"),
        )),
    )

    assert daily_runner.get_home_teams_nba_cdn("2026-11-05") == []


def test_cdn_stops_after_first_matching_date_entry(monkeypatch):
    # Documents current behavior: the loop `break`s as soon as it finds ONE
    # gameDate entry whose string matches the requested day's prefix, so a
    # second gameDate entry for the same calendar day (if the feed ever
    # split it, e.g. across a timezone boundary) would be silently ignored.
    payload = {
        "leagueSchedule": {
            "gameDates": [
                {"gameDate": "11/05/2026 00:00:00", "games": [
                    {"homeTeam": {"teamTricode": "GSW", "teamCity": "Golden State"}, "awayTeam": {"teamCity": "LA"}},
                ]},
                {"gameDate": "11/05/2026 06:00:00", "games": [
                    {"homeTeam": {"teamTricode": "BOS", "teamCity": "Boston"}, "awayTeam": {"teamCity": "Miami"}},
                ]},
            ]
        }
    }
    monkeypatch.setattr(daily_runner.requests, "get", lambda *a, **k: FakeResponse(payload))

    assert daily_runner.get_home_teams_nba_cdn("2026-11-05") == ["warriors"]


# ── get_home_teams_today() fallback orchestration ────────────────────────

def test_today_uses_espn_result_without_touching_cdn(monkeypatch):
    monkeypatch.setattr(
        daily_runner.requests, "get",
        lambda *a, **k: FakeResponse(_espn_payload(("GSW", "Golden State Warriors", "LA Lakers"))),
    )
    # If get_home_teams_today() fell through to the CDN path as well, this
    # would be called a second time with a different URL -- assert it isn't
    # by making requests.get only ever return the ESPN-shaped payload.

    assert daily_runner.get_home_teams_today("2026-11-05") == ["warriors"]


def test_today_falls_back_to_cdn_when_espn_fails(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, *a, **k):
        calls["n"] += 1
        if "espn.com" in url:
            raise RuntimeError("ESPN 403")
        return FakeResponse(_cdn_payload("11/05/2026 00:00:00", ("GSW", "Golden State", "LA")))

    monkeypatch.setattr(daily_runner.requests, "get", fake_get)

    assert daily_runner.get_home_teams_today("2026-11-05") == ["warriors"]
    assert calls["n"] == 2


def test_today_propagates_cdn_failure_when_both_sources_fail(monkeypatch):
    def fake_get(url, *a, **k):
        raise RuntimeError("ESPN 403" if "espn.com" in url else "NBA CDN 500")

    monkeypatch.setattr(daily_runner.requests, "get", fake_get)

    with pytest.raises(RuntimeError, match="NBA CDN 500"):
        daily_runner.get_home_teams_today("2026-11-05")
