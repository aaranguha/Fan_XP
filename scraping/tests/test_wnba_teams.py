"""
Tests for wnba_teams.py — the per-team config lookup and per-game folder
naming used by wnba_run_game.py and wnba_runner.py. Same shape as
nfl_teams.py (see test_nfl_teams.py) but had zero coverage of its own.
"""

import re

import pytest

from wnba_teams import ESPN_ABBR_TO_SLUG, WNBA_TEAMS, get_wnba_team, wnba_game_dir


# ── get_wnba_team() ──────────────────────────────────────────────────────────

def test_known_slug_returns_team_dict_with_slug_included():
    team = get_wnba_team("liberty")

    assert team["slug"] == "liberty"
    assert team["tm_keyword"] == "New York Liberty"
    assert team["espn_abbr"] == "NY"


def test_slug_lookup_is_case_insensitive():
    team = get_wnba_team("Liberty")

    assert team["slug"] == "liberty"


def test_unknown_slug_raises_value_error_listing_valid_options():
    with pytest.raises(ValueError) as exc_info:
        get_wnba_team("not_a_real_team")

    message = str(exc_info.value)
    assert "not_a_real_team" in message
    assert "liberty" in message


def test_all_15_teams_are_configured():
    assert len(WNBA_TEAMS) == 15


def test_every_team_dict_has_the_required_keys():
    required = {"tm_keyword", "espn_abbr"}
    for slug, cfg in WNBA_TEAMS.items():
        missing = required - cfg.keys()
        assert not missing, f"{slug} is missing keys: {missing}"


def test_no_two_teams_share_a_tm_keyword_or_espn_abbr():
    # A duplicate tm_keyword would make the Ticketmaster search ambiguous
    # about which team's games it's fetching; a duplicate espn_abbr would
    # make ESPN_ABBR_TO_SLUG (dict comprehension, last write wins) silently
    # drop a team.
    keywords = [cfg["tm_keyword"] for cfg in WNBA_TEAMS.values()]
    abbrs = [cfg["espn_abbr"] for cfg in WNBA_TEAMS.values()]

    assert len(keywords) == len(set(keywords))
    assert len(abbrs) == len(set(abbrs))


def test_abbr_to_slug_is_the_exact_inverse_of_espn_abbr():
    for slug, cfg in WNBA_TEAMS.items():
        assert ESPN_ABBR_TO_SLUG[cfg["espn_abbr"]] == slug


# ── wnba_game_dir() ──────────────────────────────────────────────────────────

def test_game_dir_has_expected_shape():
    path = wnba_game_dir("liberty", "2026-06-13", "Las Vegas Aces")

    assert path == "data/wnba/liberty/2026-06-13_las_vegas_aces_at_liberty"


def test_game_dir_strips_punctuation_from_opponent_name():
    path = wnba_game_dir("sky", "2026-06-13", "St. Louis' Team!!")

    assert path == "data/wnba/sky/2026-06-13_st_louis_team_at_sky"
    opponent_segment = path.split("/")[-1].split("_at_")[0]
    assert re.fullmatch(r"[a-z0-9_-]+", opponent_segment)


def test_game_dir_collapses_repeated_separators_without_leftover_underscores():
    path = wnba_game_dir("fever", "2026-06-13", "  Connecticut---Sun  ")

    assert path == "data/wnba/fever/2026-06-13_connecticut_sun_at_fever"


def test_same_opponent_input_always_produces_the_same_dir():
    a = wnba_game_dir("storm", "2026-06-13", "Seattle Mercury")
    b = wnba_game_dir("storm", "2026-06-13", "Seattle Mercury")

    assert a == b


def test_different_opponents_on_same_date_produce_different_dirs():
    a = wnba_game_dir("mystics", "2026-06-13", "Atlanta Dream")
    b = wnba_game_dir("mystics", "2026-06-13", "Chicago Sky")

    assert a != b
