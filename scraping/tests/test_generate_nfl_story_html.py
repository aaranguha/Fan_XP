"""
Tests for generate_nfl_story.py's generate_html() -- the function that
assembles the actual HTML string a team's dashboard page ships. Before this,
only its two helpers (analyse_games(), fmt_name() -- test_generate_nfl_story.py)
and its two data loaders (test_generate_nfl_story_local_load.py,
test_generate_nfl_story_supabase_load.py) had coverage; generate_html() itself
-- the no-data placeholder branch, the Supabase-to-local-CSV fallback, the
best_game selection, and the phantom-alert banner -- had zero.

Writing these tests surfaced a real, live bug in the phantom-alert banner
(see the xfail test at the bottom): the per-game-row date formatting wraps
datetime.strptime() in a try/except and falls back to the raw string on a
parse failure, but the phantom-alert banner a few lines below calls
strptime() on the same best_game["date"] field with no such guard. A missing
or malformed date on a team's single highest-phantom game crashes the whole
generate_html() call -- and main()'s "all" mode has no per-team try/except
around that call either, so one bad record stops every other team's
dashboard from regenerating in the same run. A GitHub issue was opened per
the QA-agent's "don't fix bugs it finds" policy.
"""

import pytest

import generate_nfl_story as gns


def _game(game_date, opponent, pre_count, mid_count, noshows, phantom):
    """A game dict shaped like load_games_from_supabase()'s output -- the
    primary data path generate_html() reads from."""
    return {
        "folder": game_date,
        "meta": {"game_date": game_date, "opponent": opponent, "arena": "Levi's Stadium", "city": "Santa Clara"},
        "pre": [], "mid": [],
        "noshows": noshows,
        "pre_count": pre_count,
        "mid_count": mid_count,
        "phantom": phantom,
    }


def _patch_supabase_games(monkeypatch, games):
    monkeypatch.setattr(gns, "load_games_from_supabase", lambda slug: games)


# ── No data at all -> honest placeholder, not a broken "0 games" dashboard ──

def test_no_data_at_all_renders_placeholder_without_game_log(monkeypatch):
    _patch_supabase_games(monkeypatch, [])
    monkeypatch.setattr(gns, "find_all_games", lambda slug: [])

    html = gns.generate_html("testteam")

    assert "No game data collected yet" in html
    assert "Monitoring for next home game" in html
    assert "Game Log" not in html


# ── Supabase-empty falls back to local CSV folders ──────────────────────────

def test_falls_back_to_local_csvs_when_supabase_returns_nothing(monkeypatch):
    _patch_supabase_games(monkeypatch, [])
    monkeypatch.setattr(gns, "find_all_games", lambda slug: ["2026-09-14"])
    monkeypatch.setattr(gns, "load_game", lambda slug, folder: {
        "folder": folder,
        "meta": {"game_date": "2026-09-14", "opponent": "Denver Broncos", "arena": "Levi's Stadium"},
        "pre": [{}] * 100,
        "mid": [{}] * 40,
        "noshows": [{"section": "101", "price_usd": "50.0"}] * 5,
    })

    html = gns.generate_html("testteam")

    assert "Game Log" in html
    assert "Denver Broncos" in html


def test_local_fallback_not_used_when_supabase_has_data(monkeypatch):
    # If Supabase returns games, find_all_games()/load_game() must not be
    # consulted at all -- Supabase is the primary source (CLAUDE.md §4.4).
    g = _game("2026-09-14", "Denver Broncos", 100, 40, [{"price_usd": 50.0}], phantom=85.0)
    _patch_supabase_games(monkeypatch, [g])
    monkeypatch.setattr(gns, "find_all_games", lambda slug: (_ for _ in ()).throw(AssertionError("should not be called")))

    html = gns.generate_html("testteam")

    assert "Game Log" in html


# ── Games without halftime data get placeholder dashes, no phantom banner ──

def test_games_without_mid_data_show_dashes_and_no_phantom_banner(monkeypatch):
    g = _game("2026-09-14", "Denver Broncos", 100, 0, [], phantom=0.0)
    _patch_supabase_games(monkeypatch, [g])

    html = gns.generate_html("testteam")

    assert '<div class="phantom-alert">' not in html
    assert "—" in html


def test_best_game_excludes_games_without_mid_data_even_if_phantom_precomputed(monkeypatch):
    # A game with no halftime scrape (mid_count == 0) must never drive the
    # phantom-alert banner, even if something upstream left a nonzero
    # "phantom" value sitting on it.
    g = _game("2026-09-14", "Denver Broncos", 100, 0, [], phantom=9999.0)
    _patch_supabase_games(monkeypatch, [g])

    html = gns.generate_html("testteam")

    assert '<div class="phantom-alert">' not in html


# ── best_game: highest-phantom game among real candidates, not the last one ─

def test_best_game_is_highest_phantom_candidate_not_just_last_in_list(monkeypatch):
    g1 = _game("2026-09-07", "Arizona Cardinals", 100, 40, [{"price_usd": 10.0}] * 2, phantom=100.0)
    g2 = _game("2026-09-14", "Denver Broncos", 100, 40, [{"price_usd": 100.0}] * 5, phantom=500.0)
    g3 = _game("2026-09-21", "Seattle Seahawks", 100, 40, [{"price_usd": 10.0}] * 1, phantom=50.0)
    _patch_supabase_games(monkeypatch, [g1, g2, g3])

    html = gns.generate_html("testteam")

    assert "$500" in html
    assert "Denver Broncos" in html.split('<div class="phantom-alert">')[1].split("</div>")[0]


def test_no_phantom_alert_when_every_game_has_zero_phantom(monkeypatch):
    # max(..., default=None) must not crash on an empty candidate sequence
    # when every has_mid game's phantom is 0 (the generator excludes
    # phantom == 0 candidates explicitly, so this must fall to the default).
    g = _game("2026-09-14", "Denver Broncos", 100, 40, [], phantom=0.0)
    _patch_supabase_games(monkeypatch, [g])

    html = gns.generate_html("testteam")

    assert '<div class="phantom-alert">' not in html


# ── Per-game-row date formatting tolerates a malformed date (has a guard) ──

def test_row_level_malformed_date_falls_back_to_raw_string_not_a_crash(monkeypatch):
    # This game is deliberately NOT the best_game (phantom stays low and a
    # second, higher-phantom, well-formed-date game is also present) so only
    # the per-row try/except path is exercised, not the unguarded banner one.
    bad_date_game = _game("not-a-date", "Arizona Cardinals", 100, 40, [{"price_usd": 10.0}], phantom=45.0)
    good_game = _game("2026-09-14", "Denver Broncos", 100, 40, [{"price_usd": 100.0}] * 5, phantom=500.0)
    _patch_supabase_games(monkeypatch, [bad_date_game, good_game])

    html = gns.generate_html("testteam")

    assert "not-a-date" in html  # raw string preserved instead of raising


# ── Real bug: the phantom-alert banner's date formatting has no such guard ─

@pytest.mark.xfail(
    reason=(
        "Real, live bug in generate_nfl_story.generate_html(): the phantom-alert "
        "banner does `datetime.strptime(best_game['date'], '%Y-%m-%d')` with no "
        "try/except, unlike the near-identical per-game-row date formatting a few "
        "lines above it, which wraps the same call in try/except and falls back to "
        "the raw string. best_game['date'] comes straight from "
        "load_games_from_supabase()'s `g.get('game_date', '')` (generate_nfl_story.py), "
        "so a Supabase games row with a missing/blank game_date on a team's single "
        "highest-phantom game raises ValueError and crashes generate_html() entirely "
        "-- and main()'s 'all' mode calls generate_html() for every team with no "
        "per-team try/except, so one bad record stops every other team's dashboard "
        "from regenerating in that run too. Not fixed here per the QA-agent policy "
        "of not patching bugs it finds; see the companion GitHub issue."
    ),
    strict=True,
)
def test_malformed_date_on_best_game_does_not_crash_the_whole_dashboard(monkeypatch):
    g = _game("", "Denver Broncos", 100, 40, [{"price_usd": 100.0}], phantom=135.0)
    _patch_supabase_games(monkeypatch, [g])

    html = gns.generate_html("testteam")

    assert "Game Log" in html
