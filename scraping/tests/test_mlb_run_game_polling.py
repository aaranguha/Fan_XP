"""
Tests for mlb_run_game.py's live mid-game detection: get_mlb_game_pk() and
wait_for_mid_game().

Why this matters (CLAUDE.md §4.2): ESPN's live scoreboard API is confirmed
blocked (403) from the runner machine, so NFL and WNBA always fall back to
their fixed-minute timer in practice. MLB is the ONLY league that gets real
live-clock detection, because it polls statsapi.mlb.com instead of ESPN.
Despite being the one league where this polling logic actually fires in
production, it had zero test coverage — nothing exercised the gamePk lookup,
the "elapsed < 30 min, just keep sleeping" guard, the per-poll inning check,
the live-poll-exception-is-not-fatal behavior, or the hard fallback deadline
that fires regardless of what the live feed says.

These tests fully mock requests.get, time.sleep, and datetime.now (via a
datetime subclass swapped in for the module's `datetime` name) so nothing
here makes a real network call or actually sleeps.
"""

from datetime import datetime as RealDateTime, timezone, timedelta

import pytest
import requests

import mlb_run_game


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
    _now = RealDateTime(2026, 7, 4, 19, 10, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls._now


def _linescore(inning, half="Top"):
    return FakeResponse({"liveData": {"linescore": {"currentInning": inning, "inningHalf": half}}})


# ── get_mlb_game_pk() ────────────────────────────────────────────────────────

def test_get_mlb_game_pk_returns_gamepk_from_schedule(monkeypatch):
    monkeypatch.setattr(
        mlb_run_game.requests, "get",
        lambda *a, **k: FakeResponse({"dates": [{"games": [{"gamePk": 777666}]}]}),
    )
    assert mlb_run_game.get_mlb_game_pk(147, "2026-07-04") == 777666


def test_get_mlb_game_pk_returns_none_when_no_games_that_day(monkeypatch):
    monkeypatch.setattr(mlb_run_game.requests, "get", lambda *a, **k: FakeResponse({"dates": []}))
    assert mlb_run_game.get_mlb_game_pk(147, "2026-07-04") is None


def test_get_mlb_game_pk_returns_none_when_dates_present_but_games_empty(monkeypatch):
    monkeypatch.setattr(
        mlb_run_game.requests, "get",
        lambda *a, **k: FakeResponse({"dates": [{"games": []}]}),
    )
    assert mlb_run_game.get_mlb_game_pk(147, "2026-07-04") is None


def test_get_mlb_game_pk_swallows_network_exception(monkeypatch):
    def raise_err(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(mlb_run_game.requests, "get", raise_err)
    assert mlb_run_game.get_mlb_game_pk(147, "2026-07-04") is None


def test_get_mlb_game_pk_swallows_http_error_status(monkeypatch):
    monkeypatch.setattr(
        mlb_run_game.requests, "get",
        lambda *a, **k: FakeResponse({}, status_error=True),
    )
    assert mlb_run_game.get_mlb_game_pk(147, "2026-07-04") is None


# ── wait_for_mid_game() ───────────────────────────────────────────────────────

def test_no_gamepk_found_falls_back_to_sleep_until(monkeypatch):
    first_pitch = RealDateTime(2026, 7, 4, 19, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(mlb_run_game, "get_mlb_game_pk", lambda *a, **k: None)

    calls = {}

    def fake_sleep_until(target, label):
        calls["target"] = target
        calls["label"] = label

    monkeypatch.setattr(mlb_run_game, "sleep_until", fake_sleep_until)
    # Must return via the fallback path without ever touching the live feed.
    monkeypatch.setattr(
        mlb_run_game.requests, "get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not poll without a gamePk")),
    )

    mlb_run_game.wait_for_mid_game(first_pitch, 147, "2026-07-04")

    assert calls["label"] == "mid_game"
    assert calls["target"] == first_pitch + timedelta(hours=mlb_run_game.MID_GAME_FALLBACK_H)


def test_fallback_deadline_already_passed_returns_without_polling(monkeypatch):
    first_pitch = RealDateTime(2026, 7, 4, 19, 10, 0, tzinfo=timezone.utc)
    FakeDateTime._now = first_pitch + timedelta(hours=2)  # past the 1.25h fallback
    monkeypatch.setattr(mlb_run_game, "datetime", FakeDateTime)
    monkeypatch.setattr(mlb_run_game, "get_mlb_game_pk", lambda *a, **k: 555)
    monkeypatch.setattr(
        mlb_run_game.requests, "get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not poll once fallback deadline has passed")),
    )
    monkeypatch.setattr(
        mlb_run_game.time, "sleep",
        lambda s: (_ for _ in ()).throw(AssertionError("must not sleep once fallback deadline has passed")),
    )

    mlb_run_game.wait_for_mid_game(first_pitch, 147, "2026-07-04")  # should return cleanly


def test_elapsed_under_30_min_just_sleeps_without_polling_live_feed(monkeypatch):
    first_pitch = RealDateTime(2026, 7, 4, 19, 10, 0, tzinfo=timezone.utc)
    FakeDateTime._now = first_pitch  # elapsed == 0
    monkeypatch.setattr(mlb_run_game, "datetime", FakeDateTime)
    monkeypatch.setattr(mlb_run_game, "get_mlb_game_pk", lambda *a, **k: 555)
    monkeypatch.setattr(
        mlb_run_game.requests, "get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not poll live feed while elapsed < 30 min")),
    )

    sleep_calls = []

    class _StopLoop(Exception):
        pass

    def fake_sleep(secs):
        sleep_calls.append(secs)
        raise _StopLoop()  # breaks the infinite poll loop for the test

    monkeypatch.setattr(mlb_run_game.time, "sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        mlb_run_game.wait_for_mid_game(first_pitch, 147, "2026-07-04")

    assert sleep_calls == [60]


def test_inning_at_threshold_triggers_return_on_first_poll(monkeypatch):
    first_pitch = RealDateTime(2026, 7, 4, 19, 10, 0, tzinfo=timezone.utc)
    FakeDateTime._now = first_pitch + timedelta(minutes=31)  # past the 30-min guard
    monkeypatch.setattr(mlb_run_game, "datetime", FakeDateTime)
    monkeypatch.setattr(mlb_run_game, "get_mlb_game_pk", lambda *a, **k: 555)
    monkeypatch.setattr(mlb_run_game.requests, "get", lambda *a, **k: _linescore(mlb_run_game.MID_GAME_INNING))
    monkeypatch.setattr(
        mlb_run_game.time, "sleep",
        lambda s: (_ for _ in ()).throw(AssertionError("should return on first poll once inning threshold is met")),
    )

    mlb_run_game.wait_for_mid_game(first_pitch, 147, "2026-07-04")  # must not raise


def test_inning_below_threshold_polls_again_until_threshold_met(monkeypatch):
    first_pitch = RealDateTime(2026, 7, 4, 19, 10, 0, tzinfo=timezone.utc)
    FakeDateTime._now = first_pitch + timedelta(minutes=31)
    monkeypatch.setattr(mlb_run_game, "datetime", FakeDateTime)
    monkeypatch.setattr(mlb_run_game, "get_mlb_game_pk", lambda *a, **k: 555)

    responses = [_linescore(1), _linescore(2), _linescore(mlb_run_game.MID_GAME_INNING)]
    calls = {"get": 0}

    def fake_get(*a, **k):
        resp = responses[calls["get"]]
        calls["get"] += 1
        return resp

    monkeypatch.setattr(mlb_run_game.requests, "get", fake_get)
    sleep_calls = []
    monkeypatch.setattr(mlb_run_game.time, "sleep", lambda s: sleep_calls.append(s))

    mlb_run_game.wait_for_mid_game(first_pitch, 147, "2026-07-04")

    assert calls["get"] == 3
    assert sleep_calls == [mlb_run_game.POLL_INTERVAL_SEC, mlb_run_game.POLL_INTERVAL_SEC]


def test_live_poll_exception_is_caught_and_polling_continues(monkeypatch):
    first_pitch = RealDateTime(2026, 7, 4, 19, 10, 0, tzinfo=timezone.utc)
    FakeDateTime._now = first_pitch + timedelta(minutes=31)
    monkeypatch.setattr(mlb_run_game, "datetime", FakeDateTime)
    monkeypatch.setattr(mlb_run_game, "get_mlb_game_pk", lambda *a, **k: 555)

    calls = {"get": 0}

    def fake_get(*a, **k):
        calls["get"] += 1
        if calls["get"] == 1:
            raise RuntimeError("statsapi.mlb.com unreachable")
        return _linescore(mlb_run_game.MID_GAME_INNING)

    monkeypatch.setattr(mlb_run_game.requests, "get", fake_get)
    monkeypatch.setattr(mlb_run_game.time, "sleep", lambda s: None)

    mlb_run_game.wait_for_mid_game(first_pitch, 147, "2026-07-04")  # must not raise

    assert calls["get"] == 2


def test_bottom_half_inning_at_threshold_still_triggers(monkeypatch):
    # inningHalf isn't checked at all (only currentInning) — confirm a
    # bottom-of-inning reading at the threshold still triggers, not just top.
    first_pitch = RealDateTime(2026, 7, 4, 19, 10, 0, tzinfo=timezone.utc)
    FakeDateTime._now = first_pitch + timedelta(minutes=31)
    monkeypatch.setattr(mlb_run_game, "datetime", FakeDateTime)
    monkeypatch.setattr(mlb_run_game, "get_mlb_game_pk", lambda *a, **k: 555)
    monkeypatch.setattr(
        mlb_run_game.requests, "get",
        lambda *a, **k: _linescore(mlb_run_game.MID_GAME_INNING, half="Bottom"),
    )
    monkeypatch.setattr(
        mlb_run_game.time, "sleep",
        lambda s: (_ for _ in ()).throw(AssertionError("should return on first poll once inning threshold is met")),
    )

    mlb_run_game.wait_for_mid_game(first_pitch, 147, "2026-07-04")  # must not raise
