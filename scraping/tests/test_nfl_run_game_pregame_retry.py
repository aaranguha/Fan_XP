"""
Tests for nfl_run_game.py's run_pre_game_with_retry() — added after run
45059262868 (2026-11-22): TM put the Broncos event behind a virtual queue/
lobby page for the Broncos' entire pre-game scrape attempt, scrape_listings()
never saw an inventory XHR and raised "No inventory request captured after
retry", and main() crashed on that immediately — losing the Broncos' whole
game day even though there was 40+ minutes of slack left in the pre-game
window (kick-off - PRE_GAME_OFFSET_MIN to kick-off - MIN_PREGAME_LEAD_MIN) in
which a retry could plausibly have worked, since TM queues/bot-pressure often
clear within a few minutes.

run_pre_game_with_retry() wraps run_snapshot() with bounded retries: capped
both by a retry count (PRE_GAME_RETRY_LIMIT) and by the pre-game window's own
deadline, so it can't retry past the point where a baseline would no longer
be meaningful (MIN_PREGAME_LEAD_MIN, covered for the rest of main() by
test_nfl_run_game_main.py).

Uses the same FakeDateTime-subclass-swapped-into-the-module pattern as
test_nfl_run_game_wait_for_halftime.py, so nothing here sleeps for real or
depends on wall-clock time.
"""

from datetime import datetime as RealDateTime, timezone, timedelta

import nfl_run_game


class FakeDateTime(RealDateTime):
    _now = RealDateTime(2026, 9, 13, 18, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls._now


def _event():
    return {"name": "Kansas City Chiefs vs. Denver Broncos"}


def test_succeeds_without_retry_when_first_attempt_works(monkeypatch):
    monkeypatch.setattr(nfl_run_game, "datetime", FakeDateTime)
    kickoff = FakeDateTime._now + timedelta(hours=1)
    calls = []
    monkeypatch.setattr(nfl_run_game, "run_snapshot", lambda *a, **kw: calls.append(1) or ["row"])
    monkeypatch.setattr(nfl_run_game.time, "sleep", lambda s: (_ for _ in ()).throw(
        AssertionError("must not sleep when the first attempt succeeds")))

    rows = nfl_run_game.run_pre_game_with_retry(_event(), "url", "out.csv", "chiefs", kickoff)

    assert rows == ["row"]
    assert len(calls) == 1


def test_retries_after_a_transient_failure_then_succeeds(monkeypatch):
    # Mirrors the Broncos incident: the first attempt hits TM's queue/lobby
    # page and raises, the retry (TM's queue having cleared) succeeds.
    monkeypatch.setattr(nfl_run_game, "datetime", FakeDateTime)
    kickoff = FakeDateTime._now + timedelta(hours=1)  # 40+ min of window left
    attempts = []

    def fake_run_snapshot(event, url, snapshot, out_csv, team_slug):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("No inventory request captured after retry — TM may have changed their page.")
        return ["row"]

    monkeypatch.setattr(nfl_run_game, "run_snapshot", fake_run_snapshot)
    sleeps = []
    monkeypatch.setattr(nfl_run_game.time, "sleep", lambda s: sleeps.append(s))

    rows = nfl_run_game.run_pre_game_with_retry(_event(), "url", "out.csv", "chiefs", kickoff)

    assert rows == ["row"]
    assert len(attempts) == 2
    assert sleeps == [nfl_run_game.PRE_GAME_RETRY_BACKOFF_SEC]


def test_gives_up_after_the_retry_limit_even_with_time_left(monkeypatch):
    # A persistently failing scrape (not just one bad load) must not retry
    # forever just because the pre-game window hasn't closed yet.
    monkeypatch.setattr(nfl_run_game, "datetime", FakeDateTime)
    kickoff = FakeDateTime._now + timedelta(hours=3)  # plenty of window left
    attempts = []

    def fake_run_snapshot(*a, **kw):
        attempts.append(1)
        raise RuntimeError("boom")

    monkeypatch.setattr(nfl_run_game, "run_snapshot", fake_run_snapshot)
    sleeps = []
    monkeypatch.setattr(nfl_run_game.time, "sleep", lambda s: sleeps.append(s))

    try:
        nfl_run_game.run_pre_game_with_retry(_event(), "url", "out.csv", "chiefs", kickoff)
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "boom" in str(e)

    assert len(attempts) == nfl_run_game.PRE_GAME_RETRY_LIMIT + 1
    assert len(sleeps) == nfl_run_game.PRE_GAME_RETRY_LIMIT


def test_gives_up_early_without_sleeping_past_the_pregame_window(monkeypatch):
    # Less than one backoff's worth of window remains before
    # MIN_PREGAME_LEAD_MIN would make a baseline meaningless — must raise
    # right away instead of sleeping past that deadline.
    monkeypatch.setattr(nfl_run_game, "datetime", FakeDateTime)
    # kick-off - MIN_PREGAME_LEAD_MIN is only 60s away from "now" — less than
    # one PRE_GAME_RETRY_BACKOFF_SEC (120s) wait, so there's no room to retry.
    kickoff = FakeDateTime._now + timedelta(minutes=nfl_run_game.MIN_PREGAME_LEAD_MIN) + timedelta(seconds=60)
    attempts = []

    def fake_run_snapshot(*a, **kw):
        attempts.append(1)
        raise RuntimeError("boom")

    monkeypatch.setattr(nfl_run_game, "run_snapshot", fake_run_snapshot)
    monkeypatch.setattr(nfl_run_game.time, "sleep", lambda s: (_ for _ in ()).throw(
        AssertionError("must not sleep once the pre-game window is about to close")))

    try:
        nfl_run_game.run_pre_game_with_retry(_event(), "url", "out.csv", "chiefs", kickoff)
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "boom" in str(e)

    assert len(attempts) == 1  # gave up after the one attempt, no retry fit in the window
