"""
Tests for backfill_meta.py -- NBA's game_meta.json backfill script.

Covers the two pure-logic helpers used to turn an existing game folder name
back into structured data: slug_to_opponent_name() (reverse slug -> full
team name lookup, with a title-cased fallback for anything not in the
hardcoded SLUG_TO_FULLNAME map) and parse_folder() (regex split of a
"YYYY-MM-DD_opponent_slug_at_team_slug" folder name into (game_date,
opponent_name)).

backfill() itself and get_opponent_record() both call out to nba_api (a
live network dependency) and are out of scope for pure-logic unit tests.
"""

from backfill_meta import parse_folder, slug_to_opponent_name


# ── slug_to_opponent_name() ──────────────────────────────────────────────────

def test_known_slug_returns_mapped_full_name():
    assert slug_to_opponent_name("lakers") == "Los Angeles Lakers"
    assert slug_to_opponent_name("76ers") == "Philadelphia 76ers"
    assert slug_to_opponent_name("warriors") == "Golden State Warriors"


def test_unknown_multiword_slug_falls_back_to_title_casing_underscores():
    # Not a SLUG_TO_FULLNAME key -- falls back to replacing "_" with " "
    # and title-casing, which happens to reconstruct a real team name here
    # even though the lookup never actually matched the dict.
    assert slug_to_opponent_name("los_angeles_lakers") == "Los Angeles Lakers"


def test_unknown_single_word_slug_falls_back_to_bare_title_case():
    assert slug_to_opponent_name("some_future_team") == "Some Future Team"


# ── parse_folder() ───────────────────────────────────────────────────────────

def test_parse_folder_extracts_date_and_resolves_known_opponent_slug():
    game_date, opponent_name = parse_folder("bulls", "2026-01-10_warriors_at_bulls")

    assert game_date == "2026-01-10"
    assert opponent_name == "Golden State Warriors"


def test_parse_folder_resolves_multiword_opponent_slug_via_fallback():
    game_date, opponent_name = parse_folder(
        "pacers", "2026-03-25_los_angeles_lakers_at_pacers"
    )

    assert game_date == "2026-03-25"
    assert opponent_name == "Los Angeles Lakers"


def test_parse_folder_returns_none_none_when_date_prefix_is_missing():
    # No leading YYYY-MM-DD_ -- doesn't match the expected folder shape.
    game_date, opponent_name = parse_folder("pacers", "los_angeles_lakers_at_pacers")

    assert (game_date, opponent_name) == (None, None)


def test_parse_folder_returns_none_none_when_there_is_no_at_separator():
    game_date, opponent_name = parse_folder("pacers", "2026-03-25_some_random_folder")

    assert (game_date, opponent_name) == (None, None)


def test_parse_folder_team_slug_argument_is_not_used_to_validate_the_folder():
    # parse_folder's first argument is never actually read in its body (the
    # regex only captures what's before/after "_at_"; the trailing group is
    # discarded) -- documenting that a mismatched team_slug doesn't change
    # the result, so callers can't rely on this function to catch a
    # folder/team_slug mismatch.
    with_matching_slug = parse_folder("pacers", "2026-03-25_warriors_at_pacers")
    with_mismatched_slug = parse_folder(
        "totally_different_team", "2026-03-25_warriors_at_pacers"
    )

    assert with_matching_slug == with_mismatched_slug


def test_parse_folder_opponent_capture_is_greedy_up_to_the_last_at_separator():
    # If the opponent portion itself happened to contain the literal
    # substring "_at_", the regex's greedy ".+" backtracks to the LAST
    # "_at_" in the folder name, so everything before that becomes the
    # opponent and everything after becomes the (discarded) team portion.
    game_date, opponent_name = parse_folder(
        "home", "2026-01-01_team_at_home_at_other"
    )

    assert game_date == "2026-01-01"
    assert opponent_name == "Team At Home"
