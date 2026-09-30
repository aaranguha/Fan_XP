"""
Tests for generate_mlb_story.py's analyse_games() and fmt_name().

analyse_games() computes the exact numbers a team dashboard shows a
prospective customer: the phantom-revenue formula (no_show_ticket_value +
no_show_count * $35 concession estimate, CLAUDE.md §4.4), the no-show rate,
the averages, and the top-no-show-sections breakdown. Mirrors the shape
already covered for generate_nfl_story.py (PR #17); MLB's version had zero
test coverage of its own before this.

fmt_name() surfaced a real, live bug -- a different flavor from the
NFL "49ers" -> "49Ers" str.title() bug already filed as issue #16.
MLB_TEAMS has several slugs that are two real words concatenated with no
separator (`whitesox`, `redsox`, `bluejays`), so `slug.replace("_", " ")`
never inserts a space between them and `.title()` capitalizes the whole
blob as one word. Confirmed live in the committed output:

    docs/mlb_whitesox_story.html  -> <title>Fan XP · Whitesox Dashboard</title>, <h1>Whitesox
    docs/mlb_redsox_story.html    -> <title>Fan XP · Redsox Dashboard</title>
    docs/mlb_bluejays_story.html  -> <title>Fan XP · Bluejays Dashboard</title>

Unlike the 49ers bug (which only mangles the team when it appears as an
*opponent*, since the 49ers slug itself renders fine), this hits the team's
*own* dashboard title/h1 directly for 3 of the 30 MLB teams -- a team being
pitched would see their own name spelled wrong on their own page. Marked
xfail rather than fixed here, per the QA-agent's "don't patch bugs it finds"
policy; see the companion GitHub issue.
"""

import pytest

import generate_mlb_story as gms


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
        "meta": {"game_date": folder, "opponent": opponent, "arena": "Chase Field"},
        "pre": [],
        "mid": [],
        "noshows": noshows,
        "pre_count": pre_count,
        "mid_count": mid_count,
    }
    g["phantom"] = phantom if phantom is not None else ns_value + ns_count * gms.CONCESSION_PER_SEAT
    return g


def _ns_row(section, price):
    return {"section": section, "price_usd": price}


# ── analyse_games(): phantom revenue formula ────────────────────────────────

def test_phantom_revenue_uses_explicit_supabase_value_when_present():
    g = _game("2026-06-14", "Los Angeles Dodgers", 100, 40,
              [_ns_row("101", 50.0)], phantom=9999.0)

    stats = gms.analyse_games([g])

    assert stats["per_game"][0]["phantom"] == 9999.0


def test_phantom_revenue_recomputed_when_not_precomputed():
    g = _game("2026-06-14", "Los Angeles Dodgers", 100, 40,
              [_ns_row("101", 50.0), _ns_row("102", 30.0)])
    del g["phantom"]

    stats = gms.analyse_games([g])

    assert stats["per_game"][0]["ns_value"] == 80.0
    assert stats["per_game"][0]["phantom"] == 150.0  # 80.0 + 2 * 35


# ── analyse_games(): no-show rate ───────────────────────────────────────────

def test_noshow_rate_is_ns_count_over_pre_count():
    g = _game("2026-06-14", "Los Angeles Dodgers", 200, 50, [_ns_row("101", 20.0)] * 40)

    stats = gms.analyse_games([g])

    assert stats["per_game"][0]["noshow_rate"] == pytest.approx(40 / 200)


def test_noshow_rate_is_zero_when_pre_count_is_zero_not_a_crash():
    g = _game("2026-06-14", "Los Angeles Dodgers", 0, 0, [])

    stats = gms.analyse_games([g])

    assert stats["per_game"][0]["noshow_rate"] == 0


# ── analyse_games(): malformed price_usd doesn't crash ──────────────────────

def test_malformed_price_usd_contributes_zero_instead_of_raising():
    g = _game("2026-06-14", "Los Angeles Dodgers", 100, 40, [
        _ns_row("101", "N/A"),
        _ns_row("101", None),
        _ns_row("101", 25.0),
    ])

    stats = gms.analyse_games([g])

    assert stats["per_game"][0]["ns_count"] == 3
    assert stats["per_game"][0]["ns_value"] == 25.0


# ── analyse_games(): averages only over games with mid-game data ───────────

def test_averages_exclude_games_without_mid_data_but_total_ns_includes_them():
    with_mid    = _game("2026-06-07", "San Diego Padres", 100, 40, [_ns_row("101", 50.0)] * 10)
    without_mid = _game("2026-06-14", "Los Angeles Dodgers", 100, 0, [_ns_row("101", 999.0)] * 5)

    stats = gms.analyse_games([with_mid, without_mid])

    assert len(stats["per_game"]) == 2
    assert stats["avg_rate"] == pytest.approx(10 / 100)
    assert stats["avg_value"] == 500.0  # 10 * 50.0, the without_mid game excluded
    assert stats["total_ns"] == 15


def test_no_games_with_mid_data_gives_zeroed_averages_not_a_crash():
    g = _game("2026-06-14", "Los Angeles Dodgers", 100, 0, [])

    stats = gms.analyse_games([g])

    assert stats["avg_rate"] == 0
    assert stats["avg_value"] == 0


def test_empty_games_list_returns_zeroed_stats():
    stats = gms.analyse_games([])

    assert stats == {"per_game": [], "avg_rate": 0, "avg_value": 0, "total_ns": 0, "top_sections": []}


# ── analyse_games(): top_sections ───────────────────────────────────────────

def test_top_sections_sorted_by_no_show_count_descending():
    g = _game("2026-06-14", "Los Angeles Dodgers", 100, 40, (
        [_ns_row("101", 50.0)] * 3 +
        [_ns_row("205", 30.0)] * 7 +
        [_ns_row("310", 20.0)] * 1
    ))

    stats = gms.analyse_games([g])

    ordered = [s["section"] for s in stats["top_sections"]]
    assert ordered == ["205", "101", "310"]
    assert stats["top_sections"][0]["ns"] == 7
    assert stats["top_sections"][0]["value"] == 210.0  # 7 * 30.0


def test_top_sections_truncated_to_ten():
    rows = [_ns_row(str(i), 10.0) for i in range(15)]
    g = _game("2026-06-14", "Los Angeles Dodgers", 100, 40, rows)

    stats = gms.analyse_games([g])

    assert len(stats["top_sections"]) == 10


def test_top_sections_whitespace_is_stripped_so_same_section_is_not_split():
    g = _game("2026-06-14", "Los Angeles Dodgers", 100, 40, [
        _ns_row("101", 10.0),
        _ns_row(" 101 ", 10.0),
        _ns_row("101", 10.0),
    ])

    stats = gms.analyse_games([g])

    assert len(stats["top_sections"]) == 1
    assert stats["top_sections"][0]["section"] == "101"
    assert stats["top_sections"][0]["ns"] == 3


def test_top_sections_missing_section_key_defaults_to_unknown():
    g = _game("2026-06-14", "Los Angeles Dodgers", 100, 40, [{"price_usd": 10.0}])

    stats = gms.analyse_games([g])

    assert stats["top_sections"][0]["section"] == "Unknown"


def test_top_sections_aggregate_across_multiple_games():
    g1 = _game("2026-06-07", "San Diego Padres", 100, 40, [_ns_row("101", 10.0)] * 4)
    g2 = _game("2026-06-14", "Los Angeles Dodgers", 100, 40, [_ns_row("101", 10.0)] * 6)

    stats = gms.analyse_games([g1, g2])

    assert len(stats["top_sections"]) == 1
    assert stats["top_sections"][0]["ns"] == 10


# ── fmt_name() ───────────────────────────────────────────────────────────────

def test_fmt_name_single_word_slug():
    assert gms.fmt_name("cubs") == "Cubs"


def test_fmt_name_underscore_slug_becomes_spaced_title_case():
    # No MLB_TEAMS slug actually contains an underscore today, but fmt_name()
    # supports the general case and this is the documented/intended behavior.
    assert gms.fmt_name("kansas_city") == "Kansas City"


@pytest.mark.xfail(
    reason=(
        "Real, live bug: fmt_name() is slug.replace('_', ' ').title(), which "
        "can't insert a word boundary into a slug that concatenates two real "
        "words with no separator. 'whitesox' is a real MLB_TEAMS slug (Chicago "
        "White Sox) and renders as 'Whitesox', not 'White Sox' -- confirmed "
        "live in docs/mlb_whitesox_story.html's own <title> and <h1>, which is "
        "the team's *own* dashboard, not just an opponent mention. Not fixed "
        "here per the QA-agent policy; see the companion GitHub issue."
    ),
    strict=True,
)
def test_fmt_name_whitesox_slug_is_not_mangled_into_one_word():
    assert gms.fmt_name("whitesox") == "White Sox"


@pytest.mark.xfail(
    reason="Same concatenated-word bug as whitesox -- confirmed live in docs/mlb_redsox_story.html's own <title>.",
    strict=True,
)
def test_fmt_name_redsox_slug_is_not_mangled_into_one_word():
    assert gms.fmt_name("redsox") == "Red Sox"


@pytest.mark.xfail(
    reason="Same concatenated-word bug as whitesox -- confirmed live in docs/mlb_bluejays_story.html's own <title>.",
    strict=True,
)
def test_fmt_name_bluejays_slug_is_not_mangled_into_one_word():
    assert gms.fmt_name("bluejays") == "Blue Jays"
