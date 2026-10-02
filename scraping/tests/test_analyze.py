"""
Tests for analyze.py's cross-game analytics helpers (avg_dead_inventory,
no_show_rate_by_section, day_of_week_breakdown, opponent_strength_correlation,
bar()).

These compute the actual "phantom revenue" / no-show-rate numbers that feed
pitch material (capability_statement.html, dashboards), so correctness here
is directly product-facing, not just internal tooling. No real data is
touched - all tests build in-memory game dicts shaped like load_games()'s
output, so no Supabase/filesystem/credentials are needed.
"""

import pytest

from analyze import (
    avg_dead_inventory,
    bar,
    day_of_week_breakdown,
    no_show_rate_by_section,
    opponent_strength_correlation,
)


def make_game(
    no_show_rate=0.2,
    dead_value=1000.0,
    day_of_week="Sunday",
    opponent_draw=None,
    pre_rows=None,
    no_show_rows=None,
):
    return {
        "no_show_rate": no_show_rate,
        "dead_value": dead_value,
        "day_of_week": day_of_week,
        "opponent_draw": opponent_draw,
        "pre_rows": pre_rows or [],
        "no_show_rows": no_show_rows or [],
    }


# ── avg_dead_inventory() ─────────────────────────────────────────────────────

def test_avg_dead_inventory_averages_only_positive_values():
    games = [make_game(dead_value=1000.0), make_game(dead_value=3000.0)]
    assert avg_dead_inventory(games) == 2000.0


def test_avg_dead_inventory_excludes_zero_value_games():
    # A game with $0 dead value (no no-shows, or no-shows with no priced
    # rows) shouldn't drag the average toward zero - it's excluded outright.
    games = [make_game(dead_value=1000.0), make_game(dead_value=0.0)]
    assert avg_dead_inventory(games) == 1000.0


def test_avg_dead_inventory_empty_list_is_zero_not_a_crash():
    assert avg_dead_inventory([]) == 0


def test_avg_dead_inventory_all_zero_is_zero_not_a_crash():
    games = [make_game(dead_value=0.0), make_game(dead_value=0.0)]
    assert avg_dead_inventory(games) == 0


# ── no_show_rate_by_section() ────────────────────────────────────────────────

def test_no_show_rate_by_section_aggregates_across_games():
    games = [
        make_game(
            pre_rows=[{"section": "101"}, {"section": "101"}, {"section": "102"}],
            no_show_rows=[{"section": "101"}],
        ),
        make_game(
            pre_rows=[{"section": "101"}],
            no_show_rows=[{"section": "101"}, {"section": "102"}],
        ),
    ]
    result = no_show_rate_by_section(games)
    # section 101: 3 pre-game appearances total, 2 no-shows -> 2/3
    assert result["101"] == {"pre_count": 3, "no_show_count": 2, "no_show_rate": pytest.approx(2 / 3)}


def test_no_show_rate_by_section_section_with_no_no_shows_is_zero_rate():
    games = [make_game(pre_rows=[{"section": "200"}], no_show_rows=[])]
    result = no_show_rate_by_section(games)
    assert result["200"] == {"pre_count": 1, "no_show_count": 0, "no_show_rate": 0.0}


def test_no_show_rate_by_section_drops_sections_absent_from_pre_rows():
    # A no-show row referencing a section that never appeared in pre_rows
    # (e.g. a seat-join mismatch) is silently excluded from the result
    # entirely, rather than reported with an undefined/infinite rate.
    games = [make_game(pre_rows=[{"section": "101"}], no_show_rows=[{"section": "999"}])]
    result = no_show_rate_by_section(games)
    assert "999" not in result
    assert result["101"]["no_show_rate"] == 0.0


def test_no_show_rate_by_section_ignores_blank_and_whitespace_only_section():
    games = [
        make_game(
            pre_rows=[{"section": "101"}, {"section": ""}, {"section": "  "}],
            no_show_rows=[{"section": ""}],
        ),
    ]
    result = no_show_rate_by_section(games)
    assert list(result.keys()) == ["101"]


def test_no_show_rate_by_section_strips_whitespace_around_section_name():
    games = [make_game(pre_rows=[{"section": " 101 "}], no_show_rows=[{"section": "101"}])]
    result = no_show_rate_by_section(games)
    assert result["101"]["no_show_count"] == 1


# ── day_of_week_breakdown() ──────────────────────────────────────────────────

def test_day_of_week_breakdown_averages_rate_and_dead_value_per_day():
    games = [
        make_game(no_show_rate=0.1, dead_value=1000.0, day_of_week="Sunday"),
        make_game(no_show_rate=0.3, dead_value=3000.0, day_of_week="Sunday"),
    ]
    result = day_of_week_breakdown(games)
    assert result["Sunday"] == {
        "game_count": 2,
        "avg_no_show_rate": pytest.approx(0.2),
        "avg_dead_value": pytest.approx(2000.0),
        "total_dead_value": pytest.approx(4000.0),
    }


def test_day_of_week_breakdown_output_is_ordered_monday_through_sunday():
    games = [
        make_game(day_of_week="Sunday"),
        make_game(day_of_week="Monday"),
        make_game(day_of_week="Thursday"),
    ]
    result = day_of_week_breakdown(games)
    assert list(result.keys()) == ["Monday", "Thursday", "Sunday"]


def test_day_of_week_breakdown_drops_days_outside_the_canonical_order():
    # meta.get("day_of_week", "Unknown") falls back to "Unknown" when
    # game_meta.json is missing/incomplete - that game must not silently
    # corrupt the Monday-Sunday table, so it's dropped from the breakdown
    # rather than raising or appearing as a stray key.
    games = [make_game(day_of_week="Sunday"), make_game(day_of_week="Unknown")]
    result = day_of_week_breakdown(games)
    assert list(result.keys()) == ["Sunday"]
    assert result["Sunday"]["game_count"] == 1


def test_day_of_week_breakdown_empty_games_is_empty_dict():
    assert day_of_week_breakdown([]) == {}


# ── opponent_strength_correlation() ──────────────────────────────────────────

def test_opponent_strength_correlation_none_with_fewer_than_two_scored_games():
    assert opponent_strength_correlation([make_game(opponent_draw=7.0)]) is None
    assert opponent_strength_correlation([]) is None


def test_opponent_strength_correlation_excludes_games_without_a_draw_score():
    games = [
        make_game(opponent_draw=9.0, no_show_rate=0.05),
        make_game(opponent_draw=3.0, no_show_rate=0.40),
        make_game(opponent_draw=None, no_show_rate=0.99),
    ]
    result = opponent_strength_correlation(games)
    assert result is not None
    assert len(result["games"]) == 2


def test_opponent_strength_correlation_is_negative_when_strong_opponents_drive_fewer_no_shows():
    # Strong opponent (high draw score) -> fans show up -> low no-show rate,
    # and vice versa. This is the expected real-world direction (see
    # analyze.py's own "r < 0 means stronger opponents -> fewer no-shows").
    games = [
        make_game(opponent_draw=9.0, no_show_rate=0.05),
        make_game(opponent_draw=7.0, no_show_rate=0.20),
        make_game(opponent_draw=5.0, no_show_rate=0.35),
        make_game(opponent_draw=2.0, no_show_rate=0.60),
    ]
    result = opponent_strength_correlation(games)
    assert result["pearson_r"] < 0


def test_opponent_strength_correlation_zero_variance_draw_scores_does_not_divide_by_zero():
    # Every game has the identical opponent draw score -> sx is 0. The
    # correlation must come back as a safe 0, never a ZeroDivisionError.
    games = [
        make_game(opponent_draw=7.0, no_show_rate=0.1),
        make_game(opponent_draw=7.0, no_show_rate=0.5),
    ]
    result = opponent_strength_correlation(games)
    assert result["pearson_r"] == 0


def test_opponent_strength_correlation_bins_boundary_values_correctly():
    games = [
        make_game(opponent_draw=8.0, no_show_rate=0.1),   # High: >= 8.0
        make_game(opponent_draw=7.9, no_show_rate=0.2),   # Mid: 6.0 <= x < 8.0
        make_game(opponent_draw=6.0, no_show_rate=0.3),   # Mid: 6.0 <= x < 8.0
        make_game(opponent_draw=5.9, no_show_rate=0.4),   # Low: < 6.0
    ]
    result = opponent_strength_correlation(games)
    bins = result["bins"]
    assert bins["High (≥8.0)"]["game_count"] == 1
    assert bins["Mid (6–8)"]["game_count"] == 2
    assert bins["Low (<6)"]["game_count"] == 1


# ── bar() ─────────────────────────────────────────────────────────────────────

def test_bar_zero_rate_is_fully_empty():
    assert bar(0.0) == "░" * 20


def test_bar_full_rate_is_fully_filled():
    assert bar(1.0) == "█" * 20


def test_bar_half_rate_splits_evenly_at_default_width():
    result = bar(0.5)
    assert result == "█" * 10 + "░" * 10


def test_bar_respects_custom_width():
    assert bar(0.5, width=4) == "█" * 2 + "░" * 2


def test_bar_rounds_fractional_fill_to_nearest_block():
    # 0.33 * 20 = 6.6 -> rounds to 7 filled blocks.
    assert bar(0.33) == "█" * 7 + "░" * 13
