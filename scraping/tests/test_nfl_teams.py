"""
Tests for nfl_teams.py — the per-team config lookup and per-game folder
naming used by every NFL runner (nfl_runner.py, nfl_run_game.py) and by
backfill/generator scripts that need to locate a game's data directory.

These are small pure functions, but get_nfl_team() is the first thing that
runs for every single NFL scrape (a bad slug here fails before any network
call happens), and nfl_game_dir() decides the on-disk path every scraped
CSV and game_meta.json for a game gets written to/read from — a naming
collision or a slug that doesn't sanitize consistently would silently merge
two different games' data into one folder.
"""

import re

import pytest

from nfl_teams import NFL_TEAMS, NFL_TRICODE_TO_SLUG, get_nfl_team, nfl_game_dir


# ── get_nfl_team() ───────────────────────────────────────────────────────────

def test_known_slug_returns_team_dict_with_slug_included():
    team = get_nfl_team("chiefs")

    assert team["slug"] == "chiefs"
    assert team["tm_keyword"] == "Kansas City Chiefs"
    assert team["espn_tricode"] == "KC"


def test_slug_lookup_is_case_insensitive():
    team = get_nfl_team("Chiefs")

    assert team["slug"] == "chiefs"


def test_unknown_slug_raises_value_error_listing_valid_options():
    with pytest.raises(ValueError) as exc_info:
        get_nfl_team("not_a_real_team")

    message = str(exc_info.value)
    assert "not_a_real_team" in message
    # The error message is the only place a typo'd CLI arg gets diagnosed —
    # it must actually name a real team so the caller can fix the typo.
    assert "chiefs" in message


def test_all_32_teams_are_configured():
    assert len(NFL_TEAMS) == 32


def test_every_team_dict_has_the_required_keys():
    required = {"tm_keyword", "espn_tricode", "city"}
    for slug, cfg in NFL_TEAMS.items():
        missing = required - cfg.keys()
        assert not missing, f"{slug} is missing keys: {missing}"


def test_no_two_teams_share_a_tm_keyword_or_espn_tricode():
    # A duplicate tm_keyword would make find_next_home_game() ambiguous
    # about which team's games it's fetching; a duplicate tricode would
    # make NFL_TRICODE_TO_SLUG (built by dict comprehension, last write
    # wins) silently drop a team.
    keywords = [cfg["tm_keyword"] for cfg in NFL_TEAMS.values()]
    tricodes = [cfg["espn_tricode"] for cfg in NFL_TEAMS.values()]

    assert len(keywords) == len(set(keywords))
    assert len(tricodes) == len(set(tricodes))


def test_tricode_to_slug_is_the_exact_inverse_of_espn_tricode():
    for slug, cfg in NFL_TEAMS.items():
        assert NFL_TRICODE_TO_SLUG[cfg["espn_tricode"]] == slug


# ── nfl_game_dir() ───────────────────────────────────────────────────────────

def test_game_dir_has_expected_shape():
    path = nfl_game_dir("chiefs", "2026-09-13", "Denver Broncos")

    assert path == "data/nfl/chiefs/2026-09-13_denver_broncos_at_chiefs"


def test_game_dir_lowercases_and_underscores_the_opponent():
    path = nfl_game_dir("49ers", "2026-09-13", "Los Angeles Rams")

    assert path == "data/nfl/49ers/2026-09-13_los_angeles_rams_at_49ers"


def test_game_dir_strips_punctuation_from_opponent_name():
    # An opponent string that still has stray punctuation on it (e.g. if a
    # caller passed the un-parsed "vs. Denver Broncos" through, or a name
    # with an apostrophe) must not leak non-alnum characters into the path.
    path = nfl_game_dir("packers", "2026-09-13", "St. Louis' Team!!")

    assert path == "data/nfl/packers/2026-09-13_st_louis_team_at_packers"
    opponent_segment = path.split("/")[-1].split("_at_")[0]
    assert re.fullmatch(r"[a-z0-9_-]+", opponent_segment)


def test_game_dir_collapses_repeated_separators_without_leftover_underscores():
    # Multiple consecutive non-alnum characters (double space, hyphen +
    # space, etc.) must collapse to a single underscore, and the result must
    # not start or end with one — otherwise "Team--Name" and "Team Name"
    # would still produce different folder names for what should read as
    # the same opponent, or leave a trailing "_" before "_at_<slug>".
    path = nfl_game_dir("bears", "2026-09-13", "  Green---Bay  Packers  ")

    assert path == "data/nfl/bears/2026-09-13_green_bay_packers_at_bears"


def test_same_opponent_input_always_produces_the_same_dir():
    a = nfl_game_dir("eagles", "2026-09-13", "Dallas Cowboys")
    b = nfl_game_dir("eagles", "2026-09-13", "Dallas Cowboys")

    assert a == b


def test_different_opponents_on_same_date_produce_different_dirs():
    # Two home games for the same team on the same date (shouldn't happen in
    # reality, but the dir name is the only thing preventing two different
    # opponents' scrapes from overwriting each other's files) must not
    # collide.
    a = nfl_game_dir("eagles", "2026-09-13", "Dallas Cowboys")
    b = nfl_game_dir("eagles", "2026-09-13", "New York Giants")

    assert a != b
