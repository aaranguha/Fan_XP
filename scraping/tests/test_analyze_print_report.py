"""
Tests for analyze.py's print_report() — the human-readable cross-game report
that stitches together every other analytics helper in the module (which
already have their own direct-unit-test coverage in test_analyze.py).

print_report() itself had zero coverage before this file: nothing drove it
end-to-end, so a crash in how it assembles/sorts/formats those helpers'
output (e.g. the ">= 10 observations" section filter, the worst/best game
ranking slices, the opponent-correlation "not enough data" branch, or the
win_pct None-vs-percentage formatting) could ship unnoticed. These tests
build in-memory game dicts shaped exactly like load_games()'s real output
(see analyze.py's load_games() docstring/return value) and capture stdout,
so no filesystem/Supabase/credentials are needed.
"""

from analyze import print_report


def make_full_game(
    team="niners",
    folder="2026-09-13_rams",
    opponent="Rams",
    no_show_rate=0.2,
    no_show_count=20,
    dead_value=1000.0,
    avg_no_show_price=50.0,
    day_of_week="Sunday",
    opponent_draw=None,
    opponent_win_pct=None,
    game_appeal=None,
    pre_rows=None,
    no_show_rows=None,
):
    pre_rows = pre_rows if pre_rows is not None else [{"section": "101"}] * max(no_show_count, 1)
    no_show_rows = no_show_rows if no_show_rows is not None else [{"section": "101"}] * no_show_count
    return {
        "team": team,
        "folder": folder,
        "label": f"{opponent} @ {team.title()}  (2026-09-13)",
        "pre_count": len(pre_rows),
        "no_show_count": no_show_count,
        "no_show_rate": no_show_rate,
        "dead_value": dead_value,
        "avg_no_show_price": avg_no_show_price,
        "day_of_week": day_of_week,
        "opponent": opponent,
        "opponent_draw": opponent_draw,
        "opponent_win_pct": opponent_win_pct,
        "game_appeal": game_appeal,
        "pre_rows": pre_rows,
        "no_show_rows": no_show_rows,
    }


# ── Header / smoke ──────────────────────────────────────────────────────────

def test_prints_header_with_game_count(capsys):
    games = [make_full_game()]
    print_report(games)
    out = capsys.readouterr().out
    assert "FAN XP" in out
    assert "(1 games)" in out


# ── Section filter (>= 10 observations) ─────────────────────────────────────

def test_sections_below_ten_observations_are_excluded_from_the_table(capsys):
    # Section "202" only has 3 pre-game appearances -> must not appear in
    # the "sections with >=10 observations" table at all.
    games = [
        make_full_game(
            pre_rows=[{"section": "101"}] * 10 + [{"section": "202"}] * 3,
            no_show_rows=[{"section": "101"}] * 2,
        )
    ]
    print_report(games)
    out = capsys.readouterr().out
    sections_table = out.split("NO-SHOW RATE BY SECTION")[1].split("WORST PERFORMING GAMES")[0]
    assert "101" in sections_table
    assert "202" not in sections_table


def test_sections_with_exactly_ten_observations_are_included(capsys):
    games = [
        make_full_game(
            pre_rows=[{"section": "101"}] * 10,
            no_show_rows=[{"section": "101"}] * 5,
        )
    ]
    print_report(games)
    out = capsys.readouterr().out
    assert "101" in out


# ── Worst / best games ranking ───────────────────────────────────────────────

def test_worst_and_best_game_labels_both_appear_for_a_small_game_set(capsys):
    games = [
        make_full_game(folder="a", opponent="Rams", no_show_rate=0.9,
                        pre_rows=[{"section": "101"}] * 10, no_show_rows=[{"section": "101"}] * 9),
        make_full_game(folder="b", opponent="Seahawks", no_show_rate=0.1,
                        pre_rows=[{"section": "101"}] * 10, no_show_rows=[{"section": "101"}] * 1),
    ]
    print_report(games)
    out = capsys.readouterr().out
    assert "Rams" in out
    assert "Seahawks" in out


def test_worst_games_table_lists_highest_rate_first(capsys):
    games = [
        make_full_game(folder="a", opponent="Low", no_show_rate=0.1,
                        pre_rows=[{"section": "101"}] * 10, no_show_rows=[{"section": "101"}] * 1),
        make_full_game(folder="b", opponent="High", no_show_rate=0.9,
                        pre_rows=[{"section": "101"}] * 10, no_show_rows=[{"section": "101"}] * 9),
    ]
    print_report(games)
    out = capsys.readouterr().out
    worst_section = out.split("WORST PERFORMING GAMES")[1].split("BEST PERFORMING GAMES")[0]
    assert worst_section.index("High") < worst_section.index("Low")


# ── Day of week ───────────────────────────────────────────────────────────

def test_day_of_week_section_lists_days_in_canonical_order(capsys):
    games = [
        make_full_game(folder="a", day_of_week="Sunday",
                        pre_rows=[{"section": "101"}] * 10, no_show_rows=[{"section": "101"}] * 2),
        make_full_game(folder="b", day_of_week="Monday",
                        pre_rows=[{"section": "101"}] * 10, no_show_rows=[{"section": "101"}] * 2),
    ]
    print_report(games)
    out = capsys.readouterr().out
    dow_section = out.split("DAY OF WEEK BREAKDOWN")[1].split("OPPONENT STRENGTH")[0]
    assert dow_section.index("Monday") < dow_section.index("Sunday")


# ── Opponent strength correlation ────────────────────────────────────────

def test_fewer_than_two_scored_games_prints_not_enough_data_without_crashing(capsys):
    games = [make_full_game(opponent_draw=None)]
    print_report(games)
    out = capsys.readouterr().out
    assert "Not enough games with draw score data." in out


def test_correlation_present_reports_direction_and_strength_labels(capsys):
    games = [
        make_full_game(folder="a", opponent_draw=9.0, no_show_rate=0.05,
                        pre_rows=[{"section": "101"}] * 10, no_show_rows=[{"section": "101"}] * 1),
        make_full_game(folder="b", opponent_draw=7.0, no_show_rate=0.20,
                        pre_rows=[{"section": "101"}] * 10, no_show_rows=[{"section": "101"}] * 2),
        make_full_game(folder="c", opponent_draw=5.0, no_show_rate=0.35,
                        pre_rows=[{"section": "101"}] * 10, no_show_rows=[{"section": "101"}] * 4),
        make_full_game(folder="d", opponent_draw=2.0, no_show_rate=0.60,
                        pre_rows=[{"section": "101"}] * 10, no_show_rows=[{"section": "101"}] * 6),
    ]
    print_report(games)
    out = capsys.readouterr().out
    assert "negative" in out
    assert "strong" in out


def test_zero_variance_draw_scores_prints_positive_weak_without_crashing(capsys):
    # opponent_strength_correlation() returns pearson_r == 0 (safe divide)
    # when every scored game has the identical draw score. print_report's
    # direction/strength labeling must handle r == 0 (0 is not < 0, and
    # abs(0) is not > 0.3) without a ZeroDivisionError bubbling up here too.
    games = [
        make_full_game(folder="a", opponent_draw=7.0, no_show_rate=0.1,
                        pre_rows=[{"section": "101"}] * 10, no_show_rows=[{"section": "101"}] * 1),
        make_full_game(folder="b", opponent_draw=7.0, no_show_rate=0.5,
                        pre_rows=[{"section": "101"}] * 10, no_show_rows=[{"section": "101"}] * 5),
    ]
    print_report(games)
    out = capsys.readouterr().out
    assert "positive" in out
    assert "weak" in out


def test_opponent_win_pct_none_formats_as_na_not_a_crash(capsys):
    games = [
        make_full_game(folder="a", opponent="Rams", opponent_draw=9.0, opponent_win_pct=None,
                        pre_rows=[{"section": "101"}] * 10, no_show_rows=[{"section": "101"}] * 1),
        make_full_game(folder="b", opponent="Seahawks", opponent_draw=3.0, opponent_win_pct=0.75,
                        pre_rows=[{"section": "101"}] * 10, no_show_rows=[{"section": "101"}] * 7),
    ]
    print_report(games)
    out = capsys.readouterr().out
    detail = out.split("Game-level detail")[1]
    assert "N/A" in detail
    assert "75%" in detail
