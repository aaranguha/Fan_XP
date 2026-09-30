"""
Tests for generate_wnba_story.py's analyse_games() and fmt_name().

analyse_games() computes the same dashboard numbers already covered for
generate_nfl_story.py (PR #17) and generate_mlb_story.py, but WNBA's version
has a real shape difference worth its own coverage: it additionally computes
a per-section "share" of total no-shows and returns the raw sec_totals dict,
which the other two leagues' generators don't do.

fmt_name() is also structurally different here -- WNBA uses an explicit
slug -> display-name dict (all 15 WNBA_TEAMS slugs covered) with a
str.title() fallback for anything unmapped, rather than always title-casing
like NFL/MLB do. This sidesteps the str.title() bugs found in those two
generators (see issue #16 and the whitesox/redsox/bluejays bug in
test_generate_mlb_story.py) as long as the dict stays in sync with
WNBA_TEAMS -- covered below.

Writing the malformed-price_usd test (already covered, and passing, for
generate_nfl_story.py and generate_mlb_story.py) surfaced a real, live bug:
WNBA's analyse_games() computes ns_value with a bare, unguarded generator
expression --

    ns_value = sum(float(r.get("price_usd", 0) or 0) for r in ns)

-- with no try/except, unlike NFL's and MLB's identical-looking line (both
wrap the per-row float() in try/except ValueError/TypeError) and unlike
WNBA's own per-section loop 6 lines below it, which *does* have the same
try/except. So one malformed price_usd value in a no_shows.csv row (e.g. an
"N/A" placeholder from an incomplete scrape) crashes the entire team
dashboard generation with an unhandled ValueError, instead of degrading
gracefully like every other league's generator does. Not fixed here per the
QA-agent policy; see the companion GitHub issue.
"""

import pytest

import generate_wnba_story as gws
from wnba_teams import WNBA_TEAMS


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
        "meta": {"game_date": folder, "opponent": opponent, "arena": "Michelob ULTRA Arena"},
        "pre": [],
        "mid": [],
        "noshows": noshows,
        "pre_count": pre_count,
        "mid_count": mid_count,
    }
    g["phantom"] = phantom if phantom is not None else ns_value + ns_count * gws.CONCESSION_PER_SEAT
    return g


def _ns_row(section, price):
    return {"section": section, "price_usd": price}


# ── analyse_games(): phantom revenue formula ────────────────────────────────

def test_phantom_revenue_uses_explicit_supabase_value_when_present():
    g = _game("2026-07-14", "New York Liberty", 100, 40,
              [_ns_row("101", 50.0)], phantom=9999.0)

    stats = gws.analyse_games([g])

    assert stats["per_game"][0]["phantom"] == 9999.0


def test_phantom_revenue_recomputed_when_not_precomputed():
    g = _game("2026-07-14", "New York Liberty", 100, 40,
              [_ns_row("101", 50.0), _ns_row("102", 30.0)])
    del g["phantom"]

    stats = gws.analyse_games([g])

    assert stats["per_game"][0]["ns_value"] == 80.0
    assert stats["per_game"][0]["phantom"] == 150.0  # 80.0 + 2 * 35


# ── analyse_games(): no-show rate ───────────────────────────────────────────

def test_noshow_rate_is_ns_count_over_pre_count():
    g = _game("2026-07-14", "New York Liberty", 200, 50, [_ns_row("101", 20.0)] * 40)

    stats = gws.analyse_games([g])

    assert stats["per_game"][0]["noshow_rate"] == pytest.approx(40 / 200)


def test_noshow_rate_is_zero_when_pre_count_is_zero_not_a_crash():
    g = _game("2026-07-14", "New York Liberty", 0, 0, [])

    stats = gws.analyse_games([g])

    assert stats["per_game"][0]["noshow_rate"] == 0


# ── analyse_games(): malformed price_usd doesn't crash ──────────────────────

@pytest.mark.xfail(
    reason=(
        "Real, live bug: unlike generate_nfl_story.py and generate_mlb_story.py "
        "(both wrap this in try/except ValueError/TypeError), WNBA's "
        "analyse_games() computes ns_value as a bare "
        "'sum(float(r.get(\"price_usd\", 0) or 0) for r in ns)' with no guard -- "
        "so a single malformed price_usd value (e.g. 'N/A') raises an "
        "unhandled ValueError and crashes the whole team dashboard generation, "
        "instead of contributing 0 like every other league's generator does. "
        "Not fixed here per the QA-agent policy; see the companion GitHub issue."
    ),
    strict=True,
)
def test_malformed_price_usd_contributes_zero_instead_of_raising():
    g = _game("2026-07-14", "New York Liberty", 100, 40, [
        _ns_row("101", "N/A"),
        _ns_row("101", None),
        _ns_row("101", 25.0),
    ])

    stats = gws.analyse_games([g])

    assert stats["per_game"][0]["ns_count"] == 3
    assert stats["per_game"][0]["ns_value"] == 25.0


# ── analyse_games(): averages only over games with mid-game data ───────────

def test_averages_exclude_games_without_mid_data_but_total_ns_includes_them():
    with_mid    = _game("2026-07-07", "Indiana Fever", 100, 40, [_ns_row("101", 50.0)] * 10)
    without_mid = _game("2026-07-14", "New York Liberty", 100, 0, [_ns_row("101", 999.0)] * 5)

    stats = gws.analyse_games([with_mid, without_mid])

    assert len(stats["per_game"]) == 2
    assert stats["avg_rate"] == pytest.approx(10 / 100)
    assert stats["avg_value"] == 500.0  # 10 * 50.0, the without_mid game excluded
    assert stats["total_ns"] == 15


def test_no_games_with_mid_data_gives_zeroed_averages_not_a_crash():
    g = _game("2026-07-14", "New York Liberty", 100, 0, [])

    stats = gws.analyse_games([g])

    assert stats["avg_rate"] == 0
    assert stats["avg_value"] == 0


def test_empty_games_list_returns_zeroed_stats():
    stats = gws.analyse_games([])

    assert stats == {
        "per_game": [], "avg_rate": 0, "avg_value": 0, "total_ns": 0,
        "top_sections": [], "sec_totals": {},
    }


# ── analyse_games(): top_sections and per-section "share" ──────────────────

def test_top_sections_sorted_by_no_show_count_descending():
    g = _game("2026-07-14", "New York Liberty", 100, 40, (
        [_ns_row("101", 50.0)] * 3 +
        [_ns_row("205", 30.0)] * 7 +
        [_ns_row("310", 20.0)] * 1
    ))

    stats = gws.analyse_games([g])

    ordered = [s["section"] for s in stats["top_sections"]]
    assert ordered == ["205", "101", "310"]
    assert stats["top_sections"][0]["ns"] == 7
    assert stats["top_sections"][0]["value"] == 210.0  # 7 * 30.0


def test_section_share_is_fraction_of_total_no_shows():
    # WNBA-only field: each section's share of the grand total_ns across
    # every game, not just within that section's own game.
    g = _game("2026-07-14", "New York Liberty", 100, 40, (
        [_ns_row("101", 50.0)] * 3 +
        [_ns_row("205", 30.0)] * 1
    ))

    stats = gws.analyse_games([g])

    by_section = {s["section"]: s for s in stats["top_sections"]}
    assert by_section["101"]["share"] == pytest.approx(3 / 4)
    assert by_section["205"]["share"] == pytest.approx(1 / 4)


def test_section_share_does_not_divide_by_zero_when_no_no_shows():
    g = _game("2026-07-14", "New York Liberty", 100, 40, [])

    stats = gws.analyse_games([g])

    assert stats["total_ns"] == 0
    assert stats["top_sections"] == []
    assert stats["sec_totals"] == {}


def test_top_sections_truncated_to_ten():
    rows = [_ns_row(str(i), 10.0) for i in range(15)]
    g = _game("2026-07-14", "New York Liberty", 100, 40, rows)

    stats = gws.analyse_games([g])

    assert len(stats["top_sections"]) == 10


def test_top_sections_whitespace_is_stripped_so_same_section_is_not_split():
    g = _game("2026-07-14", "New York Liberty", 100, 40, [
        _ns_row("101", 10.0),
        _ns_row(" 101 ", 10.0),
        _ns_row("101", 10.0),
    ])

    stats = gws.analyse_games([g])

    assert len(stats["top_sections"]) == 1
    assert stats["top_sections"][0]["section"] == "101"
    assert stats["top_sections"][0]["ns"] == 3


def test_top_sections_missing_section_key_defaults_to_unknown():
    g = _game("2026-07-14", "New York Liberty", 100, 40, [{"price_usd": 10.0}])

    stats = gws.analyse_games([g])

    assert stats["top_sections"][0]["section"] == "Unknown"


def test_top_sections_aggregate_across_multiple_games():
    g1 = _game("2026-07-07", "Indiana Fever", 100, 40, [_ns_row("101", 10.0)] * 4)
    g2 = _game("2026-07-14", "New York Liberty", 100, 40, [_ns_row("101", 10.0)] * 6)

    stats = gws.analyse_games([g1, g2])

    assert len(stats["top_sections"]) == 1
    assert stats["top_sections"][0]["ns"] == 10


# ── fmt_name() ───────────────────────────────────────────────────────────────

def test_fmt_name_known_slug_uses_explicit_display_name():
    assert gws.fmt_name("aces") == "Las Vegas Aces"
    assert gws.fmt_name("sparks") == "LA Sparks"


def test_fmt_name_unknown_slug_falls_back_to_title_case():
    assert gws.fmt_name("some_future_expansion_team") == "Some Future Expansion Team"


def test_fmt_name_every_real_wnba_team_slug_is_explicitly_mapped():
    # If a new team is added to WNBA_TEAMS without a matching fmt_name()
    # entry, it would silently fall through to the generic .title() fallback
    # -- which is exactly the str.title() bug already found in the NFL
    # (issue #16) and MLB (test_generate_mlb_story.py) generators, e.g. it
    # would mangle any future concatenated-word or digit-containing slug.
    # This pins today's dict against that regression.
    names = {
        "dream": "Atlanta Dream", "sky": "Chicago Sky", "sun": "Connecticut Sun",
        "wings": "Dallas Wings", "valkyries": "Golden State Valkyries",
        "fever": "Indiana Fever", "aces": "Las Vegas Aces", "sparks": "LA Sparks",
        "lynx": "Minnesota Lynx", "liberty": "New York Liberty",
        "mercury": "Phoenix Mercury", "storm": "Seattle Storm",
        "mystics": "Washington Mystics", "fire": "Portland Fire",
        "tempo": "Toronto Tempo",
    }
    for slug in WNBA_TEAMS:
        assert slug in names, f"{slug!r} is in WNBA_TEAMS but not fmt_name()'s explicit map"
        assert gws.fmt_name(slug) == names[slug]
