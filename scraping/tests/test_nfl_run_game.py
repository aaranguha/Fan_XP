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
  - evaluate_snapshot_quality(): the three sanity gates main() runs before
    ever inserting no-shows (CLAUDE.md §4.2 MIN_HALFTIME_RATIO marketplace-
    collapse gate, and §4.3's impossible-no_shows invariant, applied here as
    a pre-insert gate rather than the post-hoc corruption check that
    check_data_integrity.py runs).
"""

from datetime import datetime, timezone

import pytest

from nfl_run_game import (
    parse_opponent_name,
    parse_clock_minutes,
    get_kickoff_utc,
    notify_scrape_status,
    evaluate_snapshot_quality,
    MIN_HALFTIME_RATIO,
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


def test_promo_suffix_after_dash_is_dropped():
    assert parse_opponent_name(
        "San Francisco 49ers vs. Arizona Cardinals - George Kittle Bobblehead"
    ) == "Arizona Cardinals"


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


# ── evaluate_snapshot_quality() ──────────────────────────────────────────────

def _seat_rows(n, section="101"):
    """n distinct seat-level rows in the same section, seats 1..n."""
    return [{"section": section, "row": "1", "seat": str(i)} for i in range(1, n + 1)]


def test_no_pregame_listings_short_circuits_before_comparing(monkeypatch):
    import nfl_run_game

    called = []
    monkeypatch.setattr(nfl_run_game, "compare", lambda pre, ht: called.append(1) or [])

    result = evaluate_snapshot_quality([], _seat_rows(5))

    assert result.verdict == "no_pregame"
    assert result.no_shows == []
    assert called == []  # compare() must never run with no baseline to compare against


def test_marketplace_collapse_matches_the_confirmed_live_incident(monkeypatch):
    # CLAUDE.md §4.2: real Seahawks vs Patriots game, 181 pre-game listings
    # collapsed to 4 at halftime (~98% "no-show") — TM's own marketplace
    # winding down, not real fan no-shows.
    import nfl_run_game

    called = []
    monkeypatch.setattr(nfl_run_game, "compare", lambda pre, ht: called.append(1) or [])

    result = evaluate_snapshot_quality(_seat_rows(181), _seat_rows(4))

    assert result.verdict == "marketplace_collapsed"
    assert result.no_shows == []
    assert called == []  # compare() must never run once the gate trips


def test_halftime_ratio_just_below_threshold_is_collapsed():
    pre_rows = _seat_rows(100)
    ht_rows = _seat_rows(14)  # 14% < 15% MIN_HALFTIME_RATIO

    result = evaluate_snapshot_quality(pre_rows, ht_rows)

    assert result.verdict == "marketplace_collapsed"


def test_halftime_ratio_exactly_at_threshold_is_not_collapsed():
    # Boundary: the gate is a strict "<", so exactly MIN_HALFTIME_RATIO is
    # the legitimate floor, not a violation.
    pre_rows = _seat_rows(100)
    ht_rows = [{"section": "101", "row": "1", "seat": str(i)} for i in range(1, 16)]  # 15%
    assert len(ht_rows) == int(100 * MIN_HALFTIME_RATIO)

    result = evaluate_snapshot_quality(pre_rows, ht_rows)

    assert result.verdict == "ok"


def test_normal_snapshots_are_ok_and_no_shows_are_the_true_intersection():
    pre_rows = _seat_rows(10)
    ht_rows = _seat_rows(6)  # seats 1-6 still listed at halftime = "sold"; 7-10 remain

    result = evaluate_snapshot_quality(pre_rows, ht_rows)

    assert result.verdict == "ok"
    assert {r["seat"] for r in result.no_shows} == {"1", "2", "3", "4", "5", "6"}


def test_impossible_no_shows_matches_the_confirmed_live_incident_shape():
    # CLAUDE.md §4.3: a 2026-09-20 git-push failure let mismatched snapshots
    # get compared, inserting no_shows=1908 against a pre-game count of only
    # 173 — impossible, since a no-show can only be a seat present in BOTH
    # snapshots. Reproduced here at smaller scale via duplicate pre-game rows
    # (the same corruption class as the duplicate-insert bug in
    # supabase_client.py): pre_game has repeated entries for the same seats,
    # so compare()'s list-comprehension intersection yields more rows than
    # either raw snapshot actually contains.
    ht_rows = _seat_rows(2)  # only 2 unique seats actually listed at halftime
    pre_rows = _seat_rows(2) * 3  # each of those 2 seats duplicated 3x pre-game

    result = evaluate_snapshot_quality(pre_rows, ht_rows)

    assert result.verdict == "impossible_no_shows"
    assert len(result.no_shows) > min(len(pre_rows), len(ht_rows))


def test_impossible_no_shows_boundary_equal_to_smaller_snapshot_is_ok():
    # Exactly at the ceiling (no_shows == min(pre, ht)) is the legitimate
    # maximum, not a violation — must be a strict ">", not ">=".
    pre_rows = _seat_rows(5)
    ht_rows = _seat_rows(5)

    result = evaluate_snapshot_quality(pre_rows, ht_rows)

    assert result.verdict == "ok"
    assert len(result.no_shows) == 5
