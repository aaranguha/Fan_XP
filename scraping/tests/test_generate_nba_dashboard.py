"""
Tests for generate_nba_dashboard.py's analyse_games() and fmt_name().

generate_nba_dashboard.py mirrors the NFL/MLB/WNBA dashboard generators
(CLAUDE.md section 4.4) but had zero test coverage of its own before this --
it's the one league whose per-team KPI dashboard was never covered after
PR #17 (NFL) and PR #30 (MLB/WNBA). analyse_games() computes the same
phantom-revenue formula, no-show rate, averages-over-games-with-mid-data,
and top-no-show-sections breakdown already covered for the other three
leagues; this pins NBA's own (structurally near-identical) copy against
regressions.

fmt_name() surfaced a real, live bug -- the same str.title() digit-boundary
issue already filed for NFL's "49ers" (issue #16): "76ers" is a real
NBA_TEAMS/TEAMS slug (Philadelphia 76ers), and
`slug.replace("_", " ").title()` capitalizes the letter immediately after
the digit run, producing "76Ers" instead of "76ers"/"76ers". Unlike the
NFL case (which only mangles the team when it appears as an *opponent*,
since the 49ers' own slug renders fine elsewhere), this hits the 76ers'
*own* dashboard title/h1 directly. Marked xfail rather than fixed here, per
the QA-agent's "don't patch bugs it finds" policy; see the companion GitHub
issue.
"""

import pytest

import generate_nba_dashboard as gnd
from teams import TEAMS


def _safe_price(row):
    try:
        return float(row.get("price_usd") or 0)
    except (ValueError, TypeError):
        return 0.0


def _game(folder, opponent, pre_count, mid_count, noshows, phantom=None):
    ns_value = sum(_safe_price(r) for r in noshows)
    ns_count = len(noshows)
    g = {
        "folder": folder,
        "meta": {"game_date": folder, "opponent": opponent, "arena": "United Center"},
        "pre": [],
        "mid": [],
        "noshows": noshows,
        "pre_count": pre_count,
        "mid_count": mid_count,
    }
    g["phantom"] = phantom if phantom is not None else ns_value + ns_count * gnd.CONCESSION_PER_SEAT
    return g


def _ns_row(section, price):
    return {"section": section, "price_usd": price}


# ── analyse_games(): phantom revenue formula ────────────────────────────────

def test_phantom_revenue_uses_explicit_supabase_value_when_present():
    g = _game("2026-11-14", "Boston Celtics", 100, 40,
              [_ns_row("101", 50.0)], phantom=9999.0)

    stats = gnd.analyse_games([g])

    assert stats["per_game"][0]["phantom"] == 9999.0


def test_phantom_revenue_recomputed_when_not_precomputed():
    g = _game("2026-11-14", "Boston Celtics", 100, 40,
              [_ns_row("101", 50.0), _ns_row("102", 30.0)])
    del g["phantom"]

    stats = gnd.analyse_games([g])

    assert stats["per_game"][0]["ns_value"] == 80.0
    assert stats["per_game"][0]["phantom"] == 150.0  # 80.0 + 2 * 35


# ── analyse_games(): no-show rate ───────────────────────────────────────────

def test_noshow_rate_is_ns_count_over_pre_count():
    g = _game("2026-11-14", "Boston Celtics", 200, 50, [_ns_row("101", 20.0)] * 40)

    stats = gnd.analyse_games([g])

    assert stats["per_game"][0]["noshow_rate"] == pytest.approx(40 / 200)


def test_noshow_rate_is_zero_when_pre_count_is_zero_not_a_crash():
    g = _game("2026-11-14", "Boston Celtics", 0, 0, [])

    stats = gnd.analyse_games([g])

    assert stats["per_game"][0]["noshow_rate"] == 0


# ── analyse_games(): malformed price_usd doesn't crash ──────────────────────

def test_malformed_price_usd_contributes_zero_instead_of_raising():
    g = _game("2026-11-14", "Boston Celtics", 100, 40, [
        _ns_row("101", "N/A"),
        _ns_row("101", None),
        _ns_row("101", 25.0),
    ])

    stats = gnd.analyse_games([g])

    assert stats["per_game"][0]["ns_count"] == 3
    assert stats["per_game"][0]["ns_value"] == 25.0


# ── analyse_games(): averages only over games with mid-game data ───────────

def test_averages_exclude_games_without_mid_data_but_total_ns_includes_them():
    with_mid    = _game("2026-11-07", "Miami Heat", 100, 40, [_ns_row("101", 50.0)] * 10)
    without_mid = _game("2026-11-14", "Boston Celtics", 100, 0, [_ns_row("101", 999.0)] * 5)

    stats = gnd.analyse_games([with_mid, without_mid])

    assert len(stats["per_game"]) == 2
    assert stats["avg_rate"] == pytest.approx(10 / 100)
    assert stats["avg_value"] == 500.0  # 10 * 50.0, the without_mid game excluded
    assert stats["total_ns"] == 15


def test_no_games_with_mid_data_gives_zeroed_averages_not_a_crash():
    g = _game("2026-11-14", "Boston Celtics", 100, 0, [])

    stats = gnd.analyse_games([g])

    assert stats["avg_rate"] == 0
    assert stats["avg_value"] == 0


def test_empty_games_list_returns_zeroed_stats():
    stats = gnd.analyse_games([])

    assert stats == {"per_game": [], "avg_rate": 0, "avg_value": 0, "total_ns": 0, "top_sections": []}


# ── analyse_games(): top_sections ───────────────────────────────────────────

def test_top_sections_sorted_by_no_show_count_descending():
    g = _game("2026-11-14", "Boston Celtics", 100, 40, (
        [_ns_row("101", 50.0)] * 3 +
        [_ns_row("205", 30.0)] * 7 +
        [_ns_row("310", 20.0)] * 1
    ))

    stats = gnd.analyse_games([g])

    ordered = [s["section"] for s in stats["top_sections"]]
    assert ordered == ["205", "101", "310"]
    assert stats["top_sections"][0]["ns"] == 7
    assert stats["top_sections"][0]["value"] == 210.0  # 7 * 30.0


def test_top_sections_truncated_to_ten():
    rows = [_ns_row(str(i), 10.0) for i in range(15)]
    g = _game("2026-11-14", "Boston Celtics", 100, 40, rows)

    stats = gnd.analyse_games([g])

    assert len(stats["top_sections"]) == 10


def test_top_sections_whitespace_is_stripped_so_same_section_is_not_split():
    g = _game("2026-11-14", "Boston Celtics", 100, 40, [
        _ns_row("101", 10.0),
        _ns_row(" 101 ", 10.0),
        _ns_row("101", 10.0),
    ])

    stats = gnd.analyse_games([g])

    assert len(stats["top_sections"]) == 1
    assert stats["top_sections"][0]["section"] == "101"
    assert stats["top_sections"][0]["ns"] == 3


def test_top_sections_missing_section_key_defaults_to_unknown():
    g = _game("2026-11-14", "Boston Celtics", 100, 40, [{"price_usd": 10.0}])

    stats = gnd.analyse_games([g])

    assert stats["top_sections"][0]["section"] == "Unknown"


def test_top_sections_aggregate_across_multiple_games():
    g1 = _game("2026-11-07", "Miami Heat", 100, 40, [_ns_row("101", 10.0)] * 4)
    g2 = _game("2026-11-14", "Boston Celtics", 100, 40, [_ns_row("101", 10.0)] * 6)

    stats = gnd.analyse_games([g1, g2])

    assert len(stats["top_sections"]) == 1
    assert stats["top_sections"][0]["ns"] == 10


# ── fmt_name() ───────────────────────────────────────────────────────────────

def test_fmt_name_single_word_slug():
    assert gnd.fmt_name("bulls") == "Bulls"


def test_fmt_name_underscore_slug_becomes_spaced_title_case():
    assert gnd.fmt_name("golden_state") == "Golden State"


def test_fmt_name_every_nba_slug_in_teams_renders_without_crashing():
    # Sanity net: fmt_name() must never raise for a slug that's actually
    # configured in TEAMS (even though, per the bug below, it doesn't
    # always render *correctly*).
    for slug, cfg in TEAMS.items():
        if "nba_city" in cfg:
            assert isinstance(gnd.fmt_name(slug), str)


@pytest.mark.xfail(
    reason=(
        "Real, live bug: fmt_name() is slug.replace('_', ' ').title(), which "
        "capitalizes the first letter after any non-alpha boundary -- including "
        "a digit run. '76ers' is a real TEAMS slug (Philadelphia 76ers) and "
        "renders as '76Ers', not '76ers', hitting the 76ers' own dashboard "
        "title/h1 directly. Same bug class as NFL's '49ers' -> '49Ers' "
        "(issue #16) and MLB's whitesox/redsox/bluejays (issue #28). Not fixed "
        "here per the QA-agent policy; see the companion GitHub issue."
    ),
    strict=True,
)
def test_fmt_name_76ers_slug_is_not_mangled():
    assert gnd.fmt_name("76ers") == "76ers"
