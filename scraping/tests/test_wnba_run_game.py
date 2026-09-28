"""
Tests for wnba_run_game.py's pure timing/parsing helpers.

Covers:
  - save_game_meta()'s opponent-name parsing. Same bug class as
    test_mlb_run_game.py documents for MLB: nfl_run_game.py's
    parse_opponent_name() was fixed to match "vs."/"vs"/"v."/bare-"v"/
    capital-"V" separators case-insensitively via regex, and to strip promo
    suffixes after " - " or ": " (CLAUDE.md §4.3), but wnba_run_game.py's
    save_game_meta() still uses the OLD case-sensitive literal-substring
    check. The capital-"V" and promo-suffix cases are marked xfail: they
    capture the correct behavior and document the still-open bug without
    failing the suite (see the companion GitHub issue).
  - save_game_meta() does NOT short-circuit on an existing game_meta.json
    the way mlb_run_game.py's / nfl_run_game.py's equivalents do — it always
    recomputes and returns fresh meta from whatever event dict it's called
    with (only the on-disk write is skipped if the file already exists).
    That's a real, intentional-looking divergence between the two runners,
    not a bug, but worth a regression test since it's easy to "fix" by
    someone assuming all three runners behave the same way.
  - get_tipoff_utc(): pulls the tip-off datetime out of a TM event.
  - parse_clock_minutes(): ESPN clock string ("2:34") -> float minutes,
    used by wait_for_halftime() to decide when Q2 has <= 2 min left.
"""

import pytest

from wnba_run_game import get_tipoff_utc, parse_clock_minutes, save_game_meta


def _event(name, local_date="2026-06-30", local_time="19:00:00", venue="Barclays Center", city="Brooklyn"):
    return {
        "name": name,
        "dates": {"start": {"localDate": local_date, "localTime": local_time}},
        "_embedded": {"venues": [{"name": venue, "city": {"name": city}}]},
    }


def _team():
    return {"slug": "liberty"}


# ── save_game_meta() opponent parsing ────────────────────────────────────────

def test_period_dot_vs_separator(tmp_path):
    meta = save_game_meta(_event("New York Liberty vs. Las Vegas Aces"), _team(), str(tmp_path))
    assert meta["opponent"] == "Las Vegas Aces"


def test_bare_vs_separator(tmp_path):
    meta = save_game_meta(_event("New York Liberty vs Las Vegas Aces"), _team(), str(tmp_path))
    assert meta["opponent"] == "Las Vegas Aces"


def test_bare_lowercase_v_separator(tmp_path):
    meta = save_game_meta(_event("New York Liberty v Las Vegas Aces"), _team(), str(tmp_path))
    assert meta["opponent"] == "Las Vegas Aces"


def test_no_recognized_separator_leaves_opponent_as_whole_name(tmp_path):
    meta = save_game_meta(_event("Some Weird Event Title"), _team(), str(tmp_path))
    assert meta["opponent"] == "Some Weird Event Title"


@pytest.mark.xfail(
    reason=(
        "Real, live-confirmed bug class (see nfl_run_game.py's parse_opponent_name "
        "docstring and CLAUDE.md §4.3): TM event names sometimes use a bare capital "
        "'V' separator ('Los Angeles Chargers V Arizona Cardinals', confirmed live). "
        "nfl_run_game.py was fixed to match case-insensitively via regex, but "
        "wnba_run_game.py's save_game_meta() still does a case-sensitive literal-"
        "substring check (`for sep in (' vs. ', ' v. ', ' vs ', ' v '):`), so a "
        "capital-V matchup name falls through and 'opponent' becomes the entire "
        "unparsed event name. Not fixed here per QA-agent policy of not patching "
        "bugs it finds; see the companion GitHub issue."
    ),
    strict=True,
)
def test_bare_uppercase_v_separator_not_recognized_bug(tmp_path):
    meta = save_game_meta(_event("New York Liberty V Las Vegas Aces"), _team(), str(tmp_path))
    assert meta["opponent"] == "Las Vegas Aces"


@pytest.mark.xfail(
    reason=(
        "Real bug: nfl_run_game.py's parse_opponent_name() strips promo suffixes "
        "after ' - ' or ': ' (confirmed live: 'San Francisco 49ers vs. Arizona "
        "Cardinals - George Kittle Bobblehead' -- CLAUDE.md §4.3), since no team "
        "name contains either. wnba_run_game.py's save_game_meta() has no "
        "equivalent trimming, so a promo suffix ends up baked into the stored "
        "opponent name (and therefore into wnba_game_dir()'s folder-name slug). "
        "Not fixed here per QA-agent policy; see the companion GitHub issue."
    ),
    strict=True,
)
def test_promo_suffix_is_not_stripped_bug(tmp_path):
    meta = save_game_meta(
        _event("New York Liberty vs. Las Vegas Aces - Fan Appreciation Night"),
        _team(),
        str(tmp_path),
    )
    assert meta["opponent"] == "Las Vegas Aces"


def test_save_game_meta_does_not_reuse_existing_file_unlike_mlb(tmp_path):
    # Unlike mlb_run_game.py's save_game_meta(), this one always recomputes
    # from the given event -- it only skips the on-disk *write* if
    # game_meta.json already exists, not the computation/return.
    first = save_game_meta(_event("New York Liberty vs. Las Vegas Aces"), _team(), str(tmp_path))
    assert first["opponent"] == "Las Vegas Aces"

    second = save_game_meta(_event("New York Liberty vs. Seattle Storm"), _team(), str(tmp_path))
    assert second["opponent"] == "Seattle Storm"


def test_save_game_meta_fields(tmp_path):
    meta = save_game_meta(
        _event("New York Liberty vs. Las Vegas Aces", local_date="2026-06-30", local_time="19:00:00"),
        _team(),
        str(tmp_path),
    )
    assert meta["home_team"] == "liberty"
    assert meta["game_date"] == "2026-06-30"
    assert meta["day_of_week"] == "Tuesday"
    assert meta["tipoff_local"] == "19:00"
    assert meta["arena"] == "Barclays Center"
    assert meta["city"] == "Brooklyn"
    assert meta["league"] == "wnba"


# ── get_tipoff_utc() ──────────────────────────────────────────────────────────

def test_get_tipoff_utc_parses_zulu_datetime():
    from datetime import datetime, timezone

    event = {"dates": {"start": {"dateTime": "2026-06-30T23:00:00Z"}}}
    assert get_tipoff_utc(event) == datetime(2026, 6, 30, 23, 0, 0, tzinfo=timezone.utc)


def test_get_tipoff_utc_raises_when_datetime_missing():
    with pytest.raises(RuntimeError):
        get_tipoff_utc({"dates": {"start": {}}})


# ── parse_clock_minutes() ─────────────────────────────────────────────────────

def test_parse_clock_minutes_minutes_and_seconds():
    assert parse_clock_minutes("2:34") == pytest.approx(2 + 34 / 60)


def test_parse_clock_minutes_zero_clock():
    assert parse_clock_minutes("0:00") == 0.0


def test_parse_clock_minutes_empty_string_falls_back_to_99():
    assert parse_clock_minutes("") == 99.0


def test_parse_clock_minutes_none_falls_back_to_99():
    assert parse_clock_minutes(None) == 99.0


def test_parse_clock_minutes_unrecognized_format_falls_back_to_99():
    assert parse_clock_minutes("halftime") == 99.0
