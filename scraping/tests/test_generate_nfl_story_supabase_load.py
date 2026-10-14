"""
Tests for generate_nfl_story.py's load_games_from_supabase() — the function
that turns raw Supabase rows into the per-game dicts analyse_games() consumes
to build a team's dashboard. analyse_games() itself is covered elsewhere
(test_generate_nfl_story.py), but those tests hand it already-built game
dicts directly — the loading/aggregation logic one level down, which is
where the real phantom-revenue number (CLAUDE.md §4.4:
no_show_ticket_value + no_show_count * $35 concession estimate) actually
gets computed from Supabase data, had zero coverage before this.

These tests drive load_games_from_supabase() through monkeypatched
supabase_client functions so no real network access or credentials are
needed.
"""

import generate_nfl_story as gns


def _patch_supabase(monkeypatch, games_meta, pre_counts, mid_counts, no_shows_by_id):
    monkeypatch.setattr(
        gns.supabase_client, "fetch_games_for_team", lambda slug, league="nfl": games_meta
    )
    monkeypatch.setattr(
        gns.supabase_client,
        "count_listings",
        lambda game_id, snapshot: (pre_counts if snapshot == "pre_game" else mid_counts).get(game_id, 0),
    )
    monkeypatch.setattr(
        gns.supabase_client,
        "fetch_no_shows_for_game",
        lambda game_id: no_shows_by_id.get(game_id, []),
    )


# ── Games with no pre-game scrape yet are skipped entirely ─────────────────

def test_game_with_zero_pre_count_is_skipped(monkeypatch):
    games_meta = [{"id": 1, "game_date": "2026-09-13", "opponent": "Rams"}]
    _patch_supabase(monkeypatch, games_meta, pre_counts={}, mid_counts={}, no_shows_by_id={})

    assert gns.load_games_from_supabase("49ers") == []


def test_only_games_with_pre_count_are_included(monkeypatch):
    games_meta = [
        {"id": 1, "game_date": "2026-09-13", "opponent": "Rams"},
        {"id": 2, "game_date": "2026-09-20", "opponent": "Seahawks"},
    ]
    # game 1 has no pre-game data yet; game 2 does.
    _patch_supabase(
        monkeypatch, games_meta,
        pre_counts={2: 100}, mid_counts={2: 80}, no_shows_by_id={},
    )

    result = gns.load_games_from_supabase("49ers")

    assert len(result) == 1
    assert result[0]["meta"]["opponent"] == "Seahawks"


# ── Phantom revenue formula ──────────────────────────────────────────────

def test_phantom_revenue_is_ticket_value_plus_concession_per_no_show(monkeypatch):
    games_meta = [{"id": 1, "game_date": "2026-09-13", "opponent": "Rams"}]
    no_shows = [{"price_usd": 100.0}, {"price_usd": 50.0}]
    _patch_supabase(
        monkeypatch, games_meta,
        pre_counts={1: 100}, mid_counts={1: 80}, no_shows_by_id={1: no_shows},
    )

    result = gns.load_games_from_supabase("49ers")

    # ns_value = 150, ns_count = 2 -> 150 + 2*35 = 220
    assert result[0]["phantom"] == 220.0


def test_phantom_revenue_is_zero_when_there_are_no_no_shows(monkeypatch):
    games_meta = [{"id": 1, "game_date": "2026-09-13", "opponent": "Rams"}]
    _patch_supabase(
        monkeypatch, games_meta,
        pre_counts={1: 100}, mid_counts={1: 80}, no_shows_by_id={1: []},
    )

    result = gns.load_games_from_supabase("49ers")

    assert result[0]["phantom"] == 0.0


def test_no_show_with_none_price_usd_contributes_zero_not_a_crash(monkeypatch):
    # Supabase stores price_usd as NULL when the original price couldn't be
    # parsed (see supabase_client.insert_listings' price_float fallback) --
    # the sum here must treat that as 0, not raise on float(None).
    games_meta = [{"id": 1, "game_date": "2026-09-13", "opponent": "Rams"}]
    no_shows = [{"price_usd": None}, {"price_usd": 40.0}]
    _patch_supabase(
        monkeypatch, games_meta,
        pre_counts={1: 100}, mid_counts={1: 80}, no_shows_by_id={1: no_shows},
    )

    result = gns.load_games_from_supabase("49ers")

    # ns_value = 40, ns_count = 2 -> 40 + 2*35 = 110
    assert result[0]["phantom"] == 110.0


# ── Field mapping ─────────────────────────────────────────────────────────

def test_meta_and_counts_are_mapped_from_the_supabase_row(monkeypatch):
    games_meta = [{
        "id": 1,
        "game_date": "2026-09-13",
        "opponent": "Rams",
        "arena": "Levi's Stadium",
        "city": "Santa Clara",
        "home_team": "49ers",
        "day_of_week": "Sunday",
    }]
    no_shows = [{"price_usd": 75.0}]
    _patch_supabase(
        monkeypatch, games_meta,
        pre_counts={1: 200}, mid_counts={1: 150}, no_shows_by_id={1: no_shows},
    )

    result = gns.load_games_from_supabase("49ers")

    g = result[0]
    assert g["folder"] == "2026-09-13"
    assert g["meta"] == {
        "game_date": "2026-09-13",
        "opponent": "Rams",
        "arena": "Levi's Stadium",
        "city": "Santa Clara",
        "home_team": "49ers",
        "league": "nfl",
        "day_of_week": "Sunday",
    }
    assert g["pre_count"] == 200
    assert g["mid_count"] == 150
    assert g["noshows"] == no_shows
    # The Supabase path never fetches full listing rows, only counts.
    assert g["pre"] == []
    assert g["mid"] == []


def test_missing_optional_fields_default_to_empty_string(monkeypatch):
    games_meta = [{"id": 1, "game_date": "2026-09-13"}]  # no arena/city/day_of_week/opponent
    _patch_supabase(
        monkeypatch, games_meta,
        pre_counts={1: 10}, mid_counts={1: 5}, no_shows_by_id={},
    )

    result = gns.load_games_from_supabase("49ers")

    meta = result[0]["meta"]
    assert meta["opponent"] == ""
    assert meta["arena"] == ""
    assert meta["city"] == ""
    assert meta["home_team"] == ""
    assert meta["day_of_week"] == ""


# ── Multiple games ────────────────────────────────────────────────────────

def test_multiple_games_preserve_order_and_each_get_own_phantom_value(monkeypatch):
    games_meta = [
        {"id": 1, "game_date": "2026-09-13", "opponent": "Rams"},
        {"id": 2, "game_date": "2026-09-20", "opponent": "Seahawks"},
    ]
    _patch_supabase(
        monkeypatch, games_meta,
        pre_counts={1: 100, 2: 50},
        mid_counts={1: 80, 2: 20},
        no_shows_by_id={1: [{"price_usd": 10.0}], 2: [{"price_usd": 20.0}, {"price_usd": 30.0}]},
    )

    result = gns.load_games_from_supabase("49ers")

    assert [g["folder"] for g in result] == ["2026-09-13", "2026-09-20"]
    assert result[0]["phantom"] == 10.0 + 1 * gns.CONCESSION_PER_SEAT
    assert result[1]["phantom"] == 50.0 + 2 * gns.CONCESSION_PER_SEAT


def test_no_games_at_all_returns_empty_list(monkeypatch):
    _patch_supabase(monkeypatch, [], pre_counts={}, mid_counts={}, no_shows_by_id={})

    assert gns.load_games_from_supabase("49ers") == []
