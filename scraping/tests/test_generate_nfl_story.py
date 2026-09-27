"""
Tests for generate_nfl_story.py's analyse_games() and fmt_name().

analyse_games() computes the exact numbers a team dashboard shows a
prospective customer: the phantom-revenue formula (no_show_ticket_value +
no_show_count * $35 concession estimate, CLAUDE.md §4.4), the no-show rate,
the averages, and the top-no-show-sections breakdown. It had zero test
coverage before this -- a bug here means a wrong number in front of a team,
not just a failed assertion.

fmt_name() is the tiny helper that turns a team slug (or an already-parsed
opponent name) into its display name for dashboard titles/headers. Testing
it surfaced a real, live bug: it calls str.title(), which -- as Python's own
docs specify -- capitalizes the first letter *after any non-alphabetic
character*, digits included. So the 49ers' own slug comes back as "49Ers",
not "49ers", and "San Francisco 49ers" (a real opponent-name string TM
produces) comes back as "San Francisco 49Ers". The 49ers are exactly the
team CLAUDE.md §6 names as having live Telegram Q&A integration, so this is
live pitch-facing material, not a hypothetical. Those two cases are marked
xfail rather than fixed here (see the reasons on each) -- a GitHub issue was
opened for the dev/reliability agent to fix the underlying helper.
"""

import pytest

import generate_nfl_story as gns


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
        "meta": {"game_date": folder, "opponent": opponent, "arena": "Levi's Stadium"},
        "pre": [],
        "mid": [],
        "noshows": noshows,
        "pre_count": pre_count,
        "mid_count": mid_count,
    }
    g["phantom"] = phantom if phantom is not None else ns_value + ns_count * gns.CONCESSION_PER_SEAT
    return g


def _ns_row(section, price):
    return {"section": section, "price_usd": price}


# ── analyse_games(): phantom revenue formula ────────────────────────────────

def test_phantom_revenue_uses_explicit_supabase_value_when_present():
    # load_games_from_supabase() precomputes "phantom" itself; analyse_games
    # must use that value (g.get("phantom", <fallback>)) rather than
    # silently recomputing it from rows that path never fully loads.
    g = _game("2026-09-14", "Denver Broncos", 100, 40,
              [_ns_row("101", 50.0)], phantom=9999.0)

    stats = gns.analyse_games([g])

    assert stats["per_game"][0]["phantom"] == 9999.0


def test_phantom_revenue_recomputed_when_not_precomputed():
    # The local-CSV path (load_game) never sets "phantom" -- analyse_games
    # must compute it itself as ns_value + ns_count * $35.
    g = _game("2026-09-14", "Denver Broncos", 100, 40,
              [_ns_row("101", 50.0), _ns_row("102", 30.0)])
    del g["phantom"]

    stats = gns.analyse_games([g])

    assert stats["per_game"][0]["ns_value"] == 80.0
    assert stats["per_game"][0]["phantom"] == 150.0  # 80.0 + 2 * 35


# ── analyse_games(): no-show rate ───────────────────────────────────────────

def test_noshow_rate_is_ns_count_over_pre_count():
    g = _game("2026-09-14", "Denver Broncos", 200, 50, [_ns_row("101", 20.0)] * 40)

    stats = gns.analyse_games([g])

    assert stats["per_game"][0]["noshow_rate"] == pytest.approx(40 / 200)


def test_noshow_rate_is_zero_when_pre_count_is_zero_not_a_crash():
    g = _game("2026-09-14", "Denver Broncos", 0, 0, [])

    stats = gns.analyse_games([g])

    assert stats["per_game"][0]["noshow_rate"] == 0


# ── analyse_games(): malformed price_usd doesn't crash ──────────────────────

def test_malformed_price_usd_contributes_zero_instead_of_raising():
    g = _game("2026-09-14", "Denver Broncos", 100, 40, [
        _ns_row("101", "N/A"),
        _ns_row("101", None),
        _ns_row("101", 25.0),
    ])

    stats = gns.analyse_games([g])

    assert stats["per_game"][0]["ns_count"] == 3
    assert stats["per_game"][0]["ns_value"] == 25.0


# ── analyse_games(): averages only over games with mid-game data ───────────

def test_averages_exclude_games_without_mid_data_but_total_ns_includes_them():
    # A game with mid_count == 0 (no real halftime scrape yet) must not drag
    # down avg_rate/avg_value with a bogus 0 -- but its no-shows (if any are
    # somehow present) still count toward the raw total_ns.
    with_mid    = _game("2026-09-07", "Arizona Cardinals", 100, 40, [_ns_row("101", 50.0)] * 10)
    without_mid = _game("2026-09-14", "Denver Broncos", 100, 0, [_ns_row("101", 999.0)] * 5)

    stats = gns.analyse_games([with_mid, without_mid])

    assert len(stats["per_game"]) == 2
    # Only the with_mid game feeds the averages: 10 no-shows / 100 pre_count.
    assert stats["avg_rate"] == pytest.approx(10 / 100)
    assert stats["avg_value"] == 500.0  # 10 * 50.0, the without_mid game excluded
    # But total_ns is the raw sum across every game regardless of has_mid.
    assert stats["total_ns"] == 15


def test_no_games_with_mid_data_gives_zeroed_averages_not_a_crash():
    g = _game("2026-09-14", "Denver Broncos", 100, 0, [])

    stats = gns.analyse_games([g])

    assert stats["avg_rate"] == 0
    assert stats["avg_value"] == 0


def test_empty_games_list_returns_zeroed_stats():
    stats = gns.analyse_games([])

    assert stats == {"per_game": [], "avg_rate": 0, "avg_value": 0, "total_ns": 0, "top_sections": []}


# ── analyse_games(): top_sections ───────────────────────────────────────────

def test_top_sections_sorted_by_no_show_count_descending():
    g = _game("2026-09-14", "Denver Broncos", 100, 40, (
        [_ns_row("101", 50.0)] * 3 +
        [_ns_row("205", 30.0)] * 7 +
        [_ns_row("310", 20.0)] * 1
    ))

    stats = gns.analyse_games([g])

    ordered = [s["section"] for s in stats["top_sections"]]
    assert ordered == ["205", "101", "310"]
    assert stats["top_sections"][0]["ns"] == 7
    assert stats["top_sections"][0]["value"] == 210.0  # 7 * 30.0


def test_top_sections_truncated_to_ten():
    rows = []
    for i in range(15):
        rows.append(_ns_row(str(i), 10.0))
    g = _game("2026-09-14", "Denver Broncos", 100, 40, rows)

    stats = gns.analyse_games([g])

    assert len(stats["top_sections"]) == 10


def test_top_sections_whitespace_is_stripped_so_same_section_is_not_split():
    g = _game("2026-09-14", "Denver Broncos", 100, 40, [
        _ns_row("101", 10.0),
        _ns_row(" 101 ", 10.0),
        _ns_row("101", 10.0),
    ])

    stats = gns.analyse_games([g])

    assert len(stats["top_sections"]) == 1
    assert stats["top_sections"][0]["section"] == "101"
    assert stats["top_sections"][0]["ns"] == 3


def test_top_sections_missing_section_key_defaults_to_unknown():
    g = _game("2026-09-14", "Denver Broncos", 100, 40, [{"price_usd": 10.0}])

    stats = gns.analyse_games([g])

    assert stats["top_sections"][0]["section"] == "Unknown"


def test_top_sections_aggregate_across_multiple_games():
    g1 = _game("2026-09-07", "Arizona Cardinals", 100, 40, [_ns_row("101", 10.0)] * 4)
    g2 = _game("2026-09-14", "Denver Broncos", 100, 40, [_ns_row("101", 10.0)] * 6)

    stats = gns.analyse_games([g1, g2])

    assert len(stats["top_sections"]) == 1
    assert stats["top_sections"][0]["ns"] == 10


# ── fmt_name() ───────────────────────────────────────────────────────────────

def test_fmt_name_single_word_slug():
    assert gns.fmt_name("bills") == "Bills"


def test_fmt_name_underscore_slug_becomes_spaced_title_case():
    assert gns.fmt_name("kansas_city") == "Kansas City"


@pytest.mark.xfail(
    reason=(
        "Real, live bug: fmt_name() uses str.title(), which capitalizes the "
        "letter right after a digit. fmt_name('49ers') returns '49Ers', not "
        "'49ers' -- and '49ers' is a real NFL_TEAMS slug (scraping/nfl_teams.py), "
        "so this renders on the live 49ers dashboard's <title> and <h1> today. "
        "Not fixed here per the QA-agent policy of not patching bugs it finds; "
        "see the companion GitHub issue."
    ),
    strict=True,
)
def test_fmt_name_49ers_slug_is_not_mangled_by_title_case():
    assert gns.fmt_name("49ers") == "49ers"


@pytest.mark.xfail(
    reason=(
        "Same str.title() bug as test_fmt_name_49ers_slug_is_not_mangled_by_title_case, "
        "hitting the opponent-name call site instead of the home-team-slug one: "
        "fmt_name() is also called on g['opponent'] (generate_nfl_story.py's "
        "generate_html, per-game rows and the phantom-alert banner), and TM's own "
        "event names produce the opponent string 'San Francisco 49ers' whenever "
        "a team hosts the 49ers -- str.title() turns that into "
        "'San Francisco 49Ers' on that opponent's own dashboard page."
    ),
    strict=True,
)
def test_fmt_name_49ers_as_an_opponent_name_is_not_mangled_by_title_case():
    assert gns.fmt_name("San Francisco 49ers") == "San Francisco 49ers"
