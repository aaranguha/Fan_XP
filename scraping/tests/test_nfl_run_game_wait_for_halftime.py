"""
Tests for nfl_run_game.py's sleep_until() and wait_for_halftime() — the two
functions that decide exactly when the halftime scrape fires. Both had ZERO
test coverage before this file (confirmed via `pytest --cov`), despite being
core to NFL's active-focus pipeline (CLAUDE.md §4.2).

Why this matters: ESPN's live scoreboard API is confirmed blocked (HTTP 403)
from the runner machine (CLAUDE.md §4.2), so in production wait_for_halftime()
*always* falls through to its HALFTIME_FALLBACK_MIN fixed-minute deadline —
the live-clock-polling branches never actually fire for real NFL games today.
That makes the fallback-deadline path the one piece of this function's
behavior production currently depends on, and the live-polling branches the
ones most likely to silently rot unnoticed since nothing exercises them.
These tests cover both: the fallback deadline (reached-already, and reached
mid-poll-loop), and each live-clock branch (matching home team, non-matching
tricode, missing home competitor, period/clock threshold, and the two
network-failure-is-non-fatal paths — generic exception and raise_for_status()).

All of this fully mocks requests.get, time.sleep, and datetime.now (via a
datetime subclass swapped in for the module's `datetime` name, the same
pattern test_mlb_run_game_polling.py uses for the analogous MLB polling
function) so nothing here makes a real network call or actually sleeps.
"""

from datetime import datetime as RealDateTime, timezone, timedelta

import pytest
import requests

import nfl_run_game


class FakeResponse:
    def __init__(self, payload, status_error=False):
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error:
            raise requests.exceptions.HTTPError("500 error")

    def json(self):
        return self._payload


class FakeDateTime(RealDateTime):
    _now = RealDateTime(2026, 9, 13, 18, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls._now


class _StopLoop(Exception):
    """Raised by a fake time.sleep() to break out of the infinite poll loop."""


def _scoreboard(period, clock, home_abbr="KC", away_abbr="DEN", tricode_on="home"):
    """One ESPN scoreboard event with a configurable home/away tricode."""
    home = {"homeAway": "home", "team": {"abbreviation": home_abbr}}
    away = {"homeAway": "away", "team": {"abbreviation": away_abbr}}
    return FakeResponse({
        "events": [{
            "competitions": [{"competitors": [home, away]}],
            "status": {"period": period, "displayClock": clock},
        }]
    })


# ── sleep_until() ─────────────────────────────────────────────────────────────

def test_sleep_until_target_already_passed_does_not_sleep(monkeypatch):
    now = RealDateTime(2026, 9, 13, 18, 0, 0, tzinfo=timezone.utc)
    target = now - timedelta(minutes=5)

    monkeypatch.setattr(
        nfl_run_game.time, "sleep",
        lambda s: (_ for _ in ()).throw(AssertionError("must not sleep once the target has already passed")),
    )

    class _FixedNow(RealDateTime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(nfl_run_game, "datetime", _FixedNow)

    nfl_run_game.sleep_until(target, "halftime")  # must return cleanly, no sleep


def test_sleep_until_wait_exactly_zero_does_not_sleep(monkeypatch):
    now = RealDateTime(2026, 9, 13, 18, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(
        nfl_run_game.time, "sleep",
        lambda s: (_ for _ in ()).throw(AssertionError("wait == 0 must not sleep (boundary is `<= 0`)")),
    )

    class _FixedNow(RealDateTime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(nfl_run_game, "datetime", _FixedNow)

    nfl_run_game.sleep_until(now, "halftime")  # wait == 0 exactly


def test_sleep_until_target_in_future_sleeps_for_remaining_seconds(monkeypatch):
    now = RealDateTime(2026, 9, 13, 18, 0, 0, tzinfo=timezone.utc)
    target = now + timedelta(minutes=10)

    class _FixedNow(RealDateTime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(nfl_run_game, "datetime", _FixedNow)

    calls = []
    monkeypatch.setattr(nfl_run_game.time, "sleep", lambda s: calls.append(s))

    nfl_run_game.sleep_until(target, "pre-game")

    assert calls == [pytest.approx(600.0)]


# ── wait_for_halftime(): fallback deadline ────────────────────────────────────

def test_fallback_deadline_already_reached_returns_without_polling(monkeypatch):
    kickoff = RealDateTime(2026, 9, 13, 18, 0, 0, tzinfo=timezone.utc)
    FakeDateTime._now = kickoff + timedelta(minutes=nfl_run_game.HALFTIME_FALLBACK_MIN)
    monkeypatch.setattr(nfl_run_game, "datetime", FakeDateTime)
    monkeypatch.setattr(
        nfl_run_game.requests, "get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not poll once fallback deadline has passed")),
    )
    monkeypatch.setattr(
        nfl_run_game.time, "sleep",
        lambda s: (_ for _ in ()).throw(AssertionError("must not sleep once fallback deadline has passed")),
    )

    nfl_run_game.wait_for_halftime(kickoff, "KC")  # should return cleanly


def test_fallback_deadline_reached_mid_poll_loop(monkeypatch):
    # Live feed never matches, but each sleep() advances the fake clock by
    # POLL_INTERVAL_SEC until the fallback deadline check at the top of the
    # loop catches it — covers the deadline re-check happening on every
    # iteration, not just the very first one.
    kickoff = RealDateTime(2026, 9, 13, 18, 0, 0, tzinfo=timezone.utc)
    FakeDateTime._now = kickoff + timedelta(minutes=nfl_run_game.HALFTIME_FALLBACK_MIN - 1)
    monkeypatch.setattr(nfl_run_game, "datetime", FakeDateTime)
    monkeypatch.setattr(nfl_run_game.requests, "get", lambda *a, **k: _scoreboard(1, "PT10M00S"))

    def fake_sleep(secs):
        FakeDateTime._now = FakeDateTime._now + timedelta(seconds=secs)

    monkeypatch.setattr(nfl_run_game.time, "sleep", fake_sleep)

    nfl_run_game.wait_for_halftime(kickoff, "KC")  # must return once the deadline is crossed


# ── wait_for_halftime(): elapsed < 15 min guard ───────────────────────────────

def test_elapsed_under_15_min_sleeps_60s_without_polling_live_feed(monkeypatch):
    kickoff = RealDateTime(2026, 9, 13, 18, 0, 0, tzinfo=timezone.utc)
    FakeDateTime._now = kickoff  # elapsed == 0
    monkeypatch.setattr(nfl_run_game, "datetime", FakeDateTime)
    monkeypatch.setattr(
        nfl_run_game.requests, "get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not poll live feed while elapsed < 15 min")),
    )

    sleep_calls = []

    def fake_sleep(secs):
        sleep_calls.append(secs)
        raise _StopLoop()

    monkeypatch.setattr(nfl_run_game.time, "sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        nfl_run_game.wait_for_halftime(kickoff, "KC")

    assert sleep_calls == [60]


# ── wait_for_halftime(): live-clock matching ──────────────────────────────────

def test_home_team_at_q2_threshold_triggers_return_on_first_poll(monkeypatch):
    kickoff = RealDateTime(2026, 9, 13, 18, 0, 0, tzinfo=timezone.utc)
    FakeDateTime._now = kickoff + timedelta(minutes=20)  # past the 15-min guard
    monkeypatch.setattr(nfl_run_game, "datetime", FakeDateTime)
    monkeypatch.setattr(
        nfl_run_game.requests, "get",
        lambda *a, **k: _scoreboard(2, "PT02M00S", home_abbr="KC"),
    )
    monkeypatch.setattr(
        nfl_run_game.time, "sleep",
        lambda s: (_ for _ in ()).throw(AssertionError("should return on first poll once Q2 threshold is met")),
    )

    nfl_run_game.wait_for_halftime(kickoff, "KC")  # must not raise


def test_home_team_period2_above_threshold_keeps_polling(monkeypatch):
    kickoff = RealDateTime(2026, 9, 13, 18, 0, 0, tzinfo=timezone.utc)
    FakeDateTime._now = kickoff + timedelta(minutes=20)
    monkeypatch.setattr(nfl_run_game, "datetime", FakeDateTime)
    monkeypatch.setattr(
        nfl_run_game.requests, "get",
        lambda *a, **k: _scoreboard(2, "PT05M00S", home_abbr="KC"),  # 5 min > Q2_TRIGGER_MIN
    )

    sleep_calls = []

    def fake_sleep(secs):
        sleep_calls.append(secs)
        raise _StopLoop()

    monkeypatch.setattr(nfl_run_game.time, "sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        nfl_run_game.wait_for_halftime(kickoff, "KC")

    assert sleep_calls == [nfl_run_game.POLL_INTERVAL_SEC]


def test_non_matching_tricode_is_skipped_and_polling_continues(monkeypatch):
    kickoff = RealDateTime(2026, 9, 13, 18, 0, 0, tzinfo=timezone.utc)
    FakeDateTime._now = kickoff + timedelta(minutes=20)
    monkeypatch.setattr(nfl_run_game, "datetime", FakeDateTime)
    # Home team in this event is the Broncos, but we're polling for the Chiefs.
    monkeypatch.setattr(
        nfl_run_game.requests, "get",
        lambda *a, **k: _scoreboard(2, "PT01M00S", home_abbr="DEN"),
    )

    sleep_calls = []

    def fake_sleep(secs):
        sleep_calls.append(secs)
        raise _StopLoop()

    monkeypatch.setattr(nfl_run_game.time, "sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        nfl_run_game.wait_for_halftime(kickoff, "KC")

    assert sleep_calls == [nfl_run_game.POLL_INTERVAL_SEC]


def test_missing_home_competitor_is_skipped_and_polling_continues(monkeypatch):
    kickoff = RealDateTime(2026, 9, 13, 18, 0, 0, tzinfo=timezone.utc)
    FakeDateTime._now = kickoff + timedelta(minutes=20)
    monkeypatch.setattr(nfl_run_game, "datetime", FakeDateTime)
    # Both competitors marked "away" — no entry with homeAway == "home" at all.
    payload = FakeResponse({
        "events": [{
            "competitions": [{"competitors": [
                {"homeAway": "away", "team": {"abbreviation": "KC"}},
                {"homeAway": "away", "team": {"abbreviation": "DEN"}},
            ]}],
            "status": {"period": 2, "displayClock": "PT01M00S"},
        }]
    })
    monkeypatch.setattr(nfl_run_game.requests, "get", lambda *a, **k: payload)

    sleep_calls = []

    def fake_sleep(secs):
        sleep_calls.append(secs)
        raise _StopLoop()

    monkeypatch.setattr(nfl_run_game.time, "sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        nfl_run_game.wait_for_halftime(kickoff, "KC")

    assert sleep_calls == [nfl_run_game.POLL_INTERVAL_SEC]


def test_matching_event_found_after_non_matching_events_still_triggers(monkeypatch):
    # Multiple games on the slate at once (true every NFL Sunday) — the
    # matching team's game can be anywhere in the events list, not just first.
    kickoff = RealDateTime(2026, 9, 13, 18, 0, 0, tzinfo=timezone.utc)
    FakeDateTime._now = kickoff + timedelta(minutes=20)
    monkeypatch.setattr(nfl_run_game, "datetime", FakeDateTime)

    def make_event(home_abbr, period, clock):
        return {
            "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"abbreviation": home_abbr}},
                {"homeAway": "away", "team": {"abbreviation": "XXX"}},
            ]}],
            "status": {"period": period, "displayClock": clock},
        }

    payload = FakeResponse({"events": [
        make_event("SEA", 2, "PT08M00S"),
        make_event("KC", 2, "PT01M30S"),
        make_event("DAL", 1, "PT12M00S"),
    ]})
    monkeypatch.setattr(nfl_run_game.requests, "get", lambda *a, **k: payload)
    monkeypatch.setattr(
        nfl_run_game.time, "sleep",
        lambda s: (_ for _ in ()).throw(AssertionError("should return once the matching team's Q2 threshold is met")),
    )

    nfl_run_game.wait_for_halftime(kickoff, "KC")  # must not raise


# ── wait_for_halftime(): network failures are non-fatal ───────────────────────

def test_http_error_status_is_caught_and_polling_continues(monkeypatch):
    kickoff = RealDateTime(2026, 9, 13, 18, 0, 0, tzinfo=timezone.utc)
    FakeDateTime._now = kickoff + timedelta(minutes=20)
    monkeypatch.setattr(nfl_run_game, "datetime", FakeDateTime)
    monkeypatch.setattr(
        nfl_run_game.requests, "get",
        lambda *a, **k: FakeResponse({}, status_error=True),
    )

    sleep_calls = []

    def fake_sleep(secs):
        sleep_calls.append(secs)
        raise _StopLoop()

    monkeypatch.setattr(nfl_run_game.time, "sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        nfl_run_game.wait_for_halftime(kickoff, "KC")

    assert sleep_calls == [nfl_run_game.POLL_INTERVAL_SEC]


def test_generic_network_exception_is_caught_and_polling_continues(monkeypatch):
    # CLAUDE.md §4.2: ESPN's live scoreboard is confirmed blocked (403) from
    # the runner machine — in production this is the branch that actually
    # fires on every real NFL poll, every time, until the fallback deadline.
    kickoff = RealDateTime(2026, 9, 13, 18, 0, 0, tzinfo=timezone.utc)
    FakeDateTime._now = kickoff + timedelta(minutes=20)
    monkeypatch.setattr(nfl_run_game, "datetime", FakeDateTime)

    def raise_err(*a, **k):
        raise RuntimeError("site.api.espn.com unreachable (403)")

    monkeypatch.setattr(nfl_run_game.requests, "get", raise_err)

    sleep_calls = []

    def fake_sleep(secs):
        sleep_calls.append(secs)
        raise _StopLoop()

    monkeypatch.setattr(nfl_run_game.time, "sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        nfl_run_game.wait_for_halftime(kickoff, "KC")

    assert sleep_calls == [nfl_run_game.POLL_INTERVAL_SEC]
