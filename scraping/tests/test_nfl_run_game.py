"""
Tests for nfl_run_game.py's pure timing/parsing helpers — the game-day
runner that drives NFL's pre-game and halftime scrapes.

Covers:
  - parse_opponent_name(): the "vs."/"vs"/"v."/bare-"v"/capital-"V" TM event
    name parsing bug (see nfl_run_game.py's docstring on the function, and
    CLAUDE.md §4.3) — TM's event names use several inconsistent separators,
    and the old literal-substring check silently left `opponent` as the
    whole unparsed event name for some of them.
  - parse_clock_minutes(): ESPN live-clock string parsing used by
    wait_for_halftime() to decide when Q2 has ≤2 min left.
  - get_kickoff_utc(): pulls the kickoff datetime out of a TM event dict.
  - notify_scrape_status(): the per-game Telegram outcome message.
"""

from datetime import datetime, timezone

import pytest

from nfl_run_game import (
    parse_opponent_name,
    parse_clock_minutes,
    get_kickoff_utc,
    notify_scrape_status,
)


# ── parse_opponent_name() ────────────────────────────────────────────────────

def test_period_dot_vs_separator():
    assert parse_opponent_name("Kansas City Chiefs vs. Denver Broncos") == "Denver Broncos"


def test_bare_vs_separator():
    assert parse_opponent_name("Kansas City Chiefs vs Denver Broncos") == "Denver Broncos"


def test_period_dot_v_separator():
    assert parse_opponent_name("Kansas City Chiefs v. Denver Broncos") == "Denver Broncos"


def test_bare_lowercase_v_separator():
    # Confirmed live event name that slipped past the old literal-substring
    # check (required a period or trailing "s").
    assert parse_opponent_name("Kansas City Chiefs v Denver Broncos") == "Denver Broncos"


def test_bare_uppercase_v_separator():
    # Confirmed live event name that slipped past the old case-sensitive check.
    assert parse_opponent_name("Los Angeles Chargers V Arizona Cardinals") == "Arizona Cardinals"


def test_at_separator():
    assert parse_opponent_name("Kansas City Chiefs at Denver Broncos") == "Denver Broncos"


def test_no_recognized_separator_returns_whole_name_unchanged():
    assert parse_opponent_name("Some Weird Preseason Event Title") == "Some Weird Preseason Event Title"


def test_does_not_false_positive_on_letter_v_inside_a_word():
    # A standalone-word match must not trigger on "v" that's part of a
    # longer word (e.g. a team or venue name containing "v").
    assert parse_opponent_name("Vikings vs. Ravens") == "Ravens"


# ── parse_clock_minutes() ────────────────────────────────────────────────────

def test_espn_pt_format_minutes_and_seconds():
    assert parse_clock_minutes("PT02M34S") == pytest.approx(2 + 34 / 60)


def test_espn_pt_format_zero_clock():
    assert parse_clock_minutes("PT00M00S") == 0.0


def test_colon_format():
    assert parse_clock_minutes("2:34") == pytest.approx(2 + 34 / 60)


def test_empty_string_falls_back_to_99():
    assert parse_clock_minutes("") == 99.0


def test_none_falls_back_to_99():
    assert parse_clock_minutes(None) == 99.0


def test_unrecognized_format_falls_back_to_99():
    assert parse_clock_minutes("halftime") == 99.0


# ── get_kickoff_utc() ────────────────────────────────────────────────────────

def test_get_kickoff_utc_parses_zulu_datetime():
    event = {"dates": {"start": {"dateTime": "2026-09-13T17:00:00Z"}}}

    kickoff = get_kickoff_utc(event)

    assert kickoff == datetime(2026, 9, 13, 17, 0, 0, tzinfo=timezone.utc)


def test_get_kickoff_utc_raises_when_datetime_missing():
    event = {"dates": {"start": {"localDate": "2026-09-13"}}}

    with pytest.raises(RuntimeError):
        get_kickoff_utc(event)


def test_get_kickoff_utc_raises_on_empty_event():
    with pytest.raises(RuntimeError):
        get_kickoff_utc({})


# ── notify_scrape_status() ───────────────────────────────────────────────────

def test_notify_sends_for_any_team(monkeypatch):
    # One outcome message per game, for every team (not just 49ers/primetime).
    import nfl_run_game

    sent = []
    monkeypatch.setattr(nfl_run_game, "send_telegram", lambda msg: sent.append(msg))

    notify_scrape_status("✅ Packers vs Atlanta Falcons (2026-09-24): scraped.")

    assert sent == ["✅ Packers vs Atlanta Falcons (2026-09-24): scraped."]
