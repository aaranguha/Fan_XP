"""
Tests for teams.py -- NBA's per-team config lookup, draw-score formula, the
reverse full-name -> slug lookup, and per-game folder naming.

teams.py is the last of the four leagues' "team config" modules to get
coverage (nfl_teams.py, mlb_teams.py already have parallel logic and/or
tests). NBA's scraping workflow is currently paused (CLAUDE.md §2), but
teams.py is still live code: run_game.py and fetch_listings.py both call
game_dir() with the raw, unparsed Ticketmaster event name every time an NBA
game is scraped, and get_team()/team_draw_score() are used by daily_runner.py
and the pitch-readiness dashboard sorting.

Notably, game_dir() turns out to be an OLDER, still-live copy of the exact
opponent-separator bug class documented in CLAUDE.md §4.3 and already
regression-tested for MLB/WNBA/NFL (test_mlb_run_game.py,
test_wnba_run_game.py): it does its own inline, case-sensitive "vs."/"v."
substring check instead of parse_opponent_name()'s case-insensitive regex,
so a bare capital "V" or a bare lowercase "v" separator (both confirmed live
on real TM event names per those other tests) is not recognized here either
-- and unlike the other leagues, when the separator isn't recognized,
game_dir() drops the opponent from the folder name ENTIRELY (falls back to
just the date), which means two different opponents' home games on the same
date would silently collide and overwrite each other's data. The two bug
cases are marked xfail rather than fixed here (see the reasons on each) --
a GitHub issue was opened to track fixing this in teams.py, same as it was
for the WNBA/MLB equivalents.
"""

import pytest

from teams import (
    TEAM_DRAW,
    TEAMS,
    data_dir,
    game_dir,
    get_team,
    halftime_csv,
    halftime_seats_csv,
    no_shows_csv,
    pre_game_csv,
    pre_seats_csv,
    slug_from_fullname,
    team_draw_score,
)


# ── get_team() ────────────────────────────────────────────────────────────────

def test_known_slug_returns_team_dict_with_slug_included():
    team = get_team("lakers")

    assert team["slug"] == "lakers"
    assert team["tm_keyword"] == "Los Angeles Lakers"
    assert team["nba_city"] == "Los Angeles"


def test_slug_lookup_is_case_insensitive():
    team = get_team("Lakers")

    assert team["slug"] == "lakers"


def test_unknown_slug_raises_value_error_listing_valid_options():
    with pytest.raises(ValueError) as exc_info:
        get_team("not_a_real_team")

    message = str(exc_info.value)
    assert "not_a_real_team" in message
    assert "lakers" in message


def test_all_30_teams_are_configured():
    assert len(TEAMS) == 30


def test_every_team_dict_has_the_required_keys():
    required = {"tm_keyword", "nba_city"}
    for slug, cfg in TEAMS.items():
        missing = required - cfg.keys()
        assert not missing, f"{slug} is missing keys: {missing}"


def test_no_two_teams_share_a_tm_keyword():
    # A duplicate tm_keyword would make find_next_home_game() ambiguous
    # about which team's games it's actually fetching.
    keywords = [cfg["tm_keyword"] for cfg in TEAMS.values()]
    assert len(keywords) == len(set(keywords))


# ── data_dir() ───────────────────────────────────────────────────────────────

def test_data_dir_shape():
    assert data_dir("warriors") == "data/warriors"


# ── team_draw_score() ────────────────────────────────────────────────────────

def test_draw_score_uses_documented_formula():
    # lakers: star=10, market=10. win_pct=0.5 -> form = min(0.5*10, 10) = 5
    # draw = 10*0.5 + 10*0.3 + 5*0.2 = 5 + 3 + 1 = 9.0
    assert team_draw_score("lakers", win_pct=0.5) == 9.0


def test_draw_score_clamps_form_component_at_10_for_win_pct_above_1():
    # win_pct=1.5 would otherwise push form past 10 (15), but it's clamped
    # via min(win_pct * 10, 10) so an over-100% input can't inflate the score.
    clamped   = team_draw_score("lakers", win_pct=1.5)
    at_cap    = team_draw_score("lakers", win_pct=1.0)

    assert clamped == at_cap


def test_draw_score_for_unknown_slug_falls_back_to_neutral_5_and_5():
    # star, market = TEAM_DRAW.get(slug, (5, 5)) -- an unconfigured slug
    # (e.g. a future expansion team) must not raise, just read as average.
    score = team_draw_score("some_future_expansion_team", win_pct=0.5)

    assert score == round(5 * 0.5 + 5 * 0.3 + 5 * 0.2, 2)


def test_every_team_in_teams_has_a_draw_rating():
    # TEAM_DRAW backs the sales-facing draw score for every configured team;
    # a team present in TEAMS but missing from TEAM_DRAW would silently fall
    # back to the neutral (5, 5) default instead of its real rating.
    missing = set(TEAMS) - set(TEAM_DRAW)
    assert not missing, f"teams missing a TEAM_DRAW entry: {missing}"


# ── slug_from_fullname() ─────────────────────────────────────────────────────

def test_slug_from_fullname_exact_match_is_case_insensitive():
    assert slug_from_fullname("Los Angeles Lakers") == "lakers"
    assert slug_from_fullname("los angeles lakers") == "lakers"


def test_slug_from_fullname_falls_back_to_last_word_nickname():
    # "Trail Blazers" isn't a full tm_keyword match, but its last word
    # ("blazers") is the slug itself.
    assert slug_from_fullname("Trail Blazers") == "blazers"


def test_slug_from_fullname_nickname_fallback_handles_plural_mismatch():
    # rstrip("s") comparison lets a singular/plural mismatch between the
    # nickname and the slug still resolve.
    assert slug_from_fullname("Portland Blazer") == "blazers"


def test_slug_from_fullname_returns_none_for_empty_or_none_input():
    assert slug_from_fullname("") is None
    assert slug_from_fullname("   ") is None
    assert slug_from_fullname(None) is None


def test_slug_from_fullname_returns_none_for_unmatched_name():
    assert slug_from_fullname("Springfield Isotopes") is None


# ── CSV path helpers ─────────────────────────────────────────────────────────

def test_csv_path_helpers_are_named_consistently_under_the_game_dir():
    gdir = "data/lakers/2026-03-11_warriors_at_lakers"

    assert pre_game_csv(gdir) == f"{gdir}/pre_game.csv"
    assert halftime_csv(gdir) == f"{gdir}/halftime.csv"
    assert pre_seats_csv(gdir) == f"{gdir}/pre_game_seats.csv"
    assert halftime_seats_csv(gdir) == f"{gdir}/halftime_seats.csv"
    assert no_shows_csv(gdir) == f"{gdir}/no_shows.csv"


# ── game_dir() ────────────────────────────────────────────────────────────────

def test_game_dir_period_dot_vs_separator():
    path = game_dir("magic", "2026-03-11", "Orlando Magic vs. Cleveland Cavaliers")

    assert path == "data/magic/2026-03-11_cleveland_cavaliers_at_magic"


def test_game_dir_period_dot_v_separator():
    path = game_dir("magic", "2026-03-11", "Orlando Magic v. Cleveland Cavaliers")

    assert path == "data/magic/2026-03-11_cleveland_cavaliers_at_magic"


def test_game_dir_bare_lowercase_vs_separator():
    path = game_dir("magic", "2026-03-11", "Orlando Magic vs Cleveland Cavaliers")

    assert path == "data/magic/2026-03-11_cleveland_cavaliers_at_magic"


def test_game_dir_no_recognized_separator_falls_back_to_bare_date():
    # Documents existing (if unfortunate) behavior for a title with no
    # separator at all -- distinct from the bug cases below, which DO
    # contain a real, valid separator that just isn't recognized.
    path = game_dir("magic", "2026-03-11", "Some Weird Event Title")

    assert path == "data/magic/2026-03-11"


@pytest.mark.xfail(
    reason=(
        "Real, live-confirmed bug class (CLAUDE.md §4.3; see also "
        "test_wnba_run_game.py::test_bare_uppercase_v_separator_not_recognized_bug "
        "and test_mlb_run_game.py's equivalent): Ticketmaster event names "
        "sometimes use a bare capital 'V' separator (confirmed live: 'Los "
        "Angeles Chargers V Arizona Cardinals'). nfl_run_game.py's "
        "parse_opponent_name() was fixed to match separators case-"
        "insensitively via regex, but teams.py's game_dir() still does its "
        "own inline, case-sensitive literal-substring check "
        "(`if \" vs. \" in event_name: ... elif \" v. \" in event_name: ... "
        "else: sep = \" vs \"`), so a capital-V matchup name falls through to "
        "the bare-' vs '-only fallback, doesn't split, and game_dir() drops "
        "the opponent ENTIRELY -- collapsing to just the bare date instead of "
        "'<date>_<opponent>_at_<slug>'. Unlike the other three leagues (which "
        "just leave the raw unparsed name in the folder), this is worse: two "
        "different opponents' home games on the same date would silently "
        "collide on the same folder. Not fixed here per QA-agent policy of "
        "not patching bugs it finds; see the companion GitHub issue."
    ),
    strict=True,
)
def test_game_dir_bare_uppercase_v_separator_not_recognized_bug():
    path = game_dir("magic", "2026-03-11", "Orlando Magic V Cleveland Cavaliers")

    assert path == "data/magic/2026-03-11_cleveland_cavaliers_at_magic"


@pytest.mark.xfail(
    reason=(
        "Same bug class as test_game_dir_bare_uppercase_v_separator_not_recognized_bug "
        "above: a bare lowercase 'v' separator (no trailing period, e.g. "
        "'Orlando Magic v Cleveland Cavaliers') is also not in game_dir()'s "
        "checked separator list (only ' vs. ', ' v. ', and the ' vs ' "
        "fallback are tried -- never ' v '), so it falls through the same "
        "way and drops the opponent from the folder name. Not fixed here "
        "per QA-agent policy; see the companion GitHub issue."
    ),
    strict=True,
)
def test_game_dir_bare_lowercase_v_separator_not_recognized_bug():
    path = game_dir("magic", "2026-03-11", "Orlando Magic v Cleveland Cavaliers")

    assert path == "data/magic/2026-03-11_cleveland_cavaliers_at_magic"


def test_game_dir_strips_punctuation_from_opponent_name():
    path = game_dir("magic", "2026-03-11", "Orlando Magic vs. St. Louis' Team!!")

    assert path == "data/magic/2026-03-11_st_louis_team_at_magic"


def test_game_dir_same_opponent_input_always_produces_the_same_dir():
    a = game_dir("magic", "2026-03-11", "Orlando Magic vs. Cleveland Cavaliers")
    b = game_dir("magic", "2026-03-11", "Orlando Magic vs. Cleveland Cavaliers")

    assert a == b


def test_game_dir_different_opponents_on_same_date_produce_different_dirs():
    a = game_dir("magic", "2026-03-11", "Orlando Magic vs. Cleveland Cavaliers")
    b = game_dir("magic", "2026-03-11", "Orlando Magic vs. Boston Celtics")

    assert a != b
