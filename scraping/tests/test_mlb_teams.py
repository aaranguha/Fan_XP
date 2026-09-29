"""
Tests for mlb_teams.py — the per-team config lookup and per-game folder
naming used by mlb_run_game.py, mlb_runner.py, and the MLB dashboard
generator. Same shape as nfl_teams.py (see test_nfl_teams.py) but had zero
coverage of its own: a bad slug or a folder-naming collision here fails
silently in exactly the way the NFL duplicate-insert/corruption incidents
(CLAUDE.md §4.3) show is expensive to discover after the fact.
"""

import re

import pytest

from mlb_teams import MLB_TEAMS, MLB_TRICODE_TO_SLUG, get_mlb_team, mlb_data_dir, mlb_game_dir


# ── get_mlb_team() ───────────────────────────────────────────────────────────

def test_known_slug_returns_team_dict_with_slug_included():
    team = get_mlb_team("dodgers")

    assert team["slug"] == "dodgers"
    assert team["tm_keyword"] == "Los Angeles Dodgers"
    assert team["espn_tricode"] == "LAD"
    assert team["mlb_team_id"] == 119


def test_slug_lookup_is_case_insensitive():
    team = get_mlb_team("Dodgers")

    assert team["slug"] == "dodgers"


def test_unknown_slug_raises_value_error_listing_valid_options():
    with pytest.raises(ValueError) as exc_info:
        get_mlb_team("not_a_real_team")

    message = str(exc_info.value)
    assert "not_a_real_team" in message
    assert "dodgers" in message


def test_all_30_teams_are_configured():
    assert len(MLB_TEAMS) == 30


def test_every_team_dict_has_the_required_keys():
    required = {"tm_keyword", "espn_tricode", "mlb_team_id", "city"}
    for slug, cfg in MLB_TEAMS.items():
        missing = required - cfg.keys()
        assert not missing, f"{slug} is missing keys: {missing}"


def test_no_two_teams_share_a_tm_keyword_espn_tricode_or_mlb_team_id():
    # A duplicate tm_keyword would make the Ticketmaster search ambiguous
    # about which team's games it's fetching; a duplicate espn_tricode would
    # make MLB_TRICODE_TO_SLUG (dict comprehension, last write wins) silently
    # drop a team; a duplicate mlb_team_id would make statsapi.mlb.com live
    # clock polling (MLB's only real live-clock detection per CLAUDE.md §4.2)
    # attach to the wrong team.
    keywords = [cfg["tm_keyword"] for cfg in MLB_TEAMS.values()]
    tricodes = [cfg["espn_tricode"] for cfg in MLB_TEAMS.values()]
    team_ids = [cfg["mlb_team_id"] for cfg in MLB_TEAMS.values()]

    assert len(keywords) == len(set(keywords))
    assert len(tricodes) == len(set(tricodes))
    assert len(team_ids) == len(set(team_ids))


def test_tricode_to_slug_is_the_exact_inverse_of_espn_tricode():
    for slug, cfg in MLB_TEAMS.items():
        assert MLB_TRICODE_TO_SLUG[cfg["espn_tricode"]] == slug


# ── mlb_data_dir() ───────────────────────────────────────────────────────────

def test_data_dir_has_expected_shape():
    assert mlb_data_dir("dodgers") == "data/mlb/dodgers"


# ── mlb_game_dir() ───────────────────────────────────────────────────────────

def test_game_dir_has_expected_shape():
    path = mlb_game_dir("dodgers", "2026-06-13", "San Francisco Giants")

    assert path == "data/mlb/dodgers/2026-06-13_san_francisco_giants_at_dodgers"


def test_game_dir_strips_punctuation_from_opponent_name():
    path = mlb_game_dir("cubs", "2026-06-13", "St. Louis' Team!!")

    assert path == "data/mlb/cubs/2026-06-13_st_louis_team_at_cubs"
    opponent_segment = path.split("/")[-1].split("_at_")[0]
    assert re.fullmatch(r"[a-z0-9_-]+", opponent_segment)


def test_game_dir_collapses_repeated_separators_without_leftover_underscores():
    path = mlb_game_dir("braves", "2026-06-13", "  New---York  Mets  ")

    assert path == "data/mlb/braves/2026-06-13_new_york_mets_at_braves"


def test_same_opponent_input_always_produces_the_same_dir():
    a = mlb_game_dir("astros", "2026-06-13", "Texas Rangers")
    b = mlb_game_dir("astros", "2026-06-13", "Texas Rangers")

    assert a == b


def test_different_opponents_on_same_date_produce_different_dirs():
    # A single MLB team can host a doubleheader (two different games, same
    # date) — the opponent's own name is the only thing preventing those
    # scrapes from overwriting each other's on-disk data.
    a = mlb_game_dir("mets", "2026-06-13", "Atlanta Braves")
    b = mlb_game_dir("mets", "2026-06-13", "Philadelphia Phillies")

    assert a != b
