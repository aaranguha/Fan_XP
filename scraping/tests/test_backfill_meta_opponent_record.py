"""
Tests for backfill_meta.py's get_opponent_record() — NBA standings lookup
used to attach an opponent's win/loss record to a backfilled game_meta.json.

test_backfill_meta.py explicitly scoped get_opponent_record() out as "calls
out to nba_api (a live network dependency)". That's true for the real
nba_api.stats.endpoints.leaguestandings.LeagueStandings class, but
get_opponent_record() only ever uses it through a module-level attribute
(`from nba_api.stats.endpoints import leaguestandings; leaguestandings.
LeagueStandings()`), so the underlying pure logic — case-insensitive full-name
matching, the nickname fallback, the WINS/W and LOSSES/L column fallback, the
divide-by-zero guard for a 0-0 team, and the bare `except Exception: pass`
swallow — is fully testable by monkeypatching that one class attribute,
without ever making a real network call.
"""

import pandas as pd
import nba_api.stats.endpoints.leaguestandings as leaguestandings

from backfill_meta import get_opponent_record


class FakeStandingsResponse:
    def __init__(self, df):
        self._df = df

    def get_data_frames(self):
        return [self._df]


def _fake_leaguestandings(monkeypatch, rows):
    df = pd.DataFrame(rows)
    monkeypatch.setattr(
        leaguestandings, "LeagueStandings", lambda *a, **k: FakeStandingsResponse(df)
    )


# ── Exact full-name match ───────────────────────────────────────────────────

def test_exact_full_name_match_returns_wins_losses_and_win_pct(monkeypatch):
    _fake_leaguestandings(
        monkeypatch,
        [{"TeamCity": "Golden State", "TeamName": "Warriors", "WINS": 10, "LOSSES": 5}],
    )

    record = get_opponent_record("Golden State Warriors")

    assert record == {
        "opponent_wins": 10,
        "opponent_losses": 5,
        "opponent_win_pct": 0.667,
    }


def test_match_is_case_insensitive(monkeypatch):
    _fake_leaguestandings(
        monkeypatch,
        [{"TeamCity": "Golden State", "TeamName": "Warriors", "WINS": 10, "LOSSES": 5}],
    )

    assert get_opponent_record("golden state warriors") == get_opponent_record(
        "Golden State Warriors"
    )


# ── Nickname fallback ────────────────────────────────────────────────────────

def test_falls_back_to_nickname_when_full_name_does_not_match(monkeypatch):
    # The standings row's city doesn't match the opponent_name passed in
    # (e.g. a relocated/renamed franchise), but the trailing nickname does —
    # get_opponent_record() must fall back to matching on TeamName alone.
    _fake_leaguestandings(
        monkeypatch,
        [{"TeamCity": "San Francisco", "TeamName": "Warriors", "WINS": 20, "LOSSES": 10}],
    )

    record = get_opponent_record("Golden State Warriors")

    assert record == {
        "opponent_wins": 20,
        "opponent_losses": 10,
        "opponent_win_pct": 0.667,
    }


def test_no_match_at_all_returns_empty_dict(monkeypatch):
    _fake_leaguestandings(
        monkeypatch,
        [{"TeamCity": "Boston", "TeamName": "Celtics", "WINS": 10, "LOSSES": 5}],
    )

    assert get_opponent_record("Golden State Warriors") == {}


# ── WINS/LOSSES vs W/L column fallback ──────────────────────────────────────

def test_falls_back_to_w_l_columns_when_wins_losses_are_absent(monkeypatch):
    _fake_leaguestandings(
        monkeypatch,
        [{"TeamCity": "Golden State", "TeamName": "Warriors", "W": 7, "L": 3}],
    )

    record = get_opponent_record("Golden State Warriors")

    assert record == {
        "opponent_wins": 7,
        "opponent_losses": 3,
        "opponent_win_pct": 0.7,
    }


# ── Divide-by-zero guard ─────────────────────────────────────────────────────

def test_zero_games_played_does_not_divide_by_zero(monkeypatch):
    _fake_leaguestandings(
        monkeypatch,
        [{"TeamCity": "Golden State", "TeamName": "Warriors", "WINS": 0, "LOSSES": 0}],
    )

    record = get_opponent_record("Golden State Warriors")

    assert record == {
        "opponent_wins": 0,
        "opponent_losses": 0,
        "opponent_win_pct": 0.0,
    }


# ── Exception safety ─────────────────────────────────────────────────────────

def test_nba_api_exception_is_swallowed_and_returns_empty_dict(monkeypatch):
    def _raise(*_a, **_k):
        raise ConnectionError("nba.com unreachable")

    monkeypatch.setattr(leaguestandings, "LeagueStandings", _raise)

    assert get_opponent_record("Golden State Warriors") == {}


def test_malformed_data_frame_is_swallowed_and_returns_empty_dict(monkeypatch):
    # A standings response missing the TeamCity/TeamName columns the
    # function assumes (df["TeamCity"] + " " + df["TeamName"]) would raise
    # a KeyError -- that must be caught by the function's own try/except,
    # not propagate out as a crash.
    df = pd.DataFrame([{"SomeOtherColumn": "value"}])
    monkeypatch.setattr(
        leaguestandings, "LeagueStandings", lambda *a, **k: FakeStandingsResponse(df)
    )

    assert get_opponent_record("Golden State Warriors") == {}
