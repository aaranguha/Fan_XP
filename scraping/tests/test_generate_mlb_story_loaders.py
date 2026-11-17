"""
Tests for generate_mlb_story.py's game loaders — find_all_games(),
load_csv_rows(), load_game() (the on-disk fallback path) and
load_games_from_supabase() (the primary data source, per CLAUDE.md §4.4).

analyse_games() already has thorough coverage (test_generate_mlb_story.py),
but those tests hand it already-built game dicts directly — the loading
layer underneath, which is where the real phantom-revenue number actually
gets computed from Supabase rows (or assembled from on-disk CSVs), had zero
coverage of its own. The equivalent NFL functions are fully covered
(test_generate_nfl_story_local_load.py, test_generate_nfl_story_supabase_load.py);
MLB's versions never got the same treatment even though the dashboards they
build (docs/mlb_{slug}_story.html) are still live and are exactly the kind
of "real historical capture" pitch material CLAUDE.md §2 leans on.

One real difference from NFL worth a dedicated test: MLB's on-disk snapshot
file is "mid_game.csv" (MLB/WNBA convention), not "halftime.csv" (NFL's) —
CLAUDE.md §4.3 documents a real incident where this exact
halftime/mid_game naming mismatch silently broke a dashboard. A regression
here guards the MLB side of that convention.

These tests drive load_games_from_supabase() through monkeypatched
supabase_client functions and load_game()/find_all_games() against a
tmp_path fake data/ tree, so no real network access, credentials, or
scraped data is needed.
"""

import json
import os

import generate_mlb_story as gms


def _write_csv(path, header, rows):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# ── find_all_games() ─────────────────────────────────────────────────────────

def test_find_all_games_returns_empty_list_when_team_dir_does_not_exist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert gms.find_all_games("cubs") == []


def test_find_all_games_returns_empty_list_when_team_dir_exists_but_has_no_games(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("data/mlb/cubs")

    assert gms.find_all_games("cubs") == []


def test_find_all_games_only_includes_folders_with_pre_game_csv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gdir = "data/mlb/cubs/2026-06-14_dodgers_at_cubs"
    os.makedirs(gdir)
    _write_csv(f"{gdir}/pre_game.csv", ["section"], [{"section": "101"}])
    # Folder exists (e.g. a crashed run) but never got a pre-game scrape.
    os.makedirs("data/mlb/cubs/2026-06-21_padres_at_cubs")
    with open("data/mlb/cubs/readme.txt", "w") as f:
        f.write("not a game folder")

    assert gms.find_all_games("cubs") == ["2026-06-14_dodgers_at_cubs"]


def test_find_all_games_results_are_sorted_regardless_of_creation_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for folder in ["2026-06-21_padres_at_cubs", "2026-06-07_braves_at_cubs", "2026-06-14_dodgers_at_cubs"]:
        gdir = f"data/mlb/cubs/{folder}"
        os.makedirs(gdir)
        _write_csv(f"{gdir}/pre_game.csv", ["section"], [{"section": "101"}])

    assert gms.find_all_games("cubs") == [
        "2026-06-07_braves_at_cubs",
        "2026-06-14_dodgers_at_cubs",
        "2026-06-21_padres_at_cubs",
    ]


# ── load_csv_rows() ───────────────────────────────────────────────────────────

def test_load_csv_rows_returns_empty_list_when_file_does_not_exist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert gms.load_csv_rows("data/mlb/cubs/nope/pre_game.csv") == []


def test_load_csv_rows_returns_empty_list_for_header_only_csv(tmp_path):
    path = tmp_path / "pre_game.csv"
    _write_csv(str(path), ["section", "row", "seat", "price_usd"], [])

    assert gms.load_csv_rows(str(path)) == []


def test_load_csv_rows_parses_rows_as_dicts(tmp_path):
    path = tmp_path / "pre_game.csv"
    _write_csv(
        str(path),
        ["section", "row", "seat", "price_usd"],
        [{"section": "101", "row": "5", "seat": "12", "price_usd": "89.00"}],
    )

    rows = gms.load_csv_rows(str(path))

    assert rows == [{"section": "101", "row": "5", "seat": "12", "price_usd": "89.00"}]


# ── load_game() ───────────────────────────────────────────────────────────────

def test_load_game_returns_none_when_game_meta_json_is_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gdir = "data/mlb/cubs/2026-06-14_dodgers_at_cubs"
    os.makedirs(gdir)
    _write_csv(f"{gdir}/pre_game.csv", ["section"], [{"section": "101"}])

    assert gms.load_game("cubs", "2026-06-14_dodgers_at_cubs") is None


def test_load_game_pre_game_only_folder_loads_with_empty_mid_and_noshows(tmp_path, monkeypatch):
    # Realistic in-progress state: pre-game snapshot taken, mid-game snapshot
    # not yet reached. Must not raise.
    monkeypatch.chdir(tmp_path)
    gdir = "data/mlb/cubs/2026-06-14_dodgers_at_cubs"
    os.makedirs(gdir)
    meta = {"home_team": "cubs", "opponent": "Dodgers", "game_date": "2026-06-14", "league": "mlb"}
    with open(f"{gdir}/game_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    _write_csv(f"{gdir}/pre_game.csv", ["section", "price_usd"], [{"section": "101", "price_usd": "89.00"}])

    game = gms.load_game("cubs", "2026-06-14_dodgers_at_cubs")

    assert game["folder"] == "2026-06-14_dodgers_at_cubs"
    assert game["meta"] == meta
    assert game["pre"] == [{"section": "101", "price_usd": "89.00"}]
    assert game["mid"] == []
    assert game["noshows"] == []


def test_load_game_reads_mid_game_csv_not_halftime_csv(tmp_path, monkeypatch):
    # Regression guard for CLAUDE.md §4.3's documented halftime/mid_game
    # naming mismatch: MLB's on-disk convention is "mid_game.csv". A file
    # named "halftime.csv" (NFL's convention) sitting in the same folder
    # must be silently ignored, not picked up as MLB's mid-game snapshot.
    monkeypatch.chdir(tmp_path)
    gdir = "data/mlb/cubs/2026-06-14_dodgers_at_cubs"
    os.makedirs(gdir)
    meta = {"home_team": "cubs", "opponent": "Dodgers", "game_date": "2026-06-14", "league": "mlb"}
    with open(f"{gdir}/game_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    _write_csv(f"{gdir}/pre_game.csv", ["section"], [{"section": "101"}])
    _write_csv(f"{gdir}/halftime.csv", ["section"], [{"section": "999"}])  # wrong name, must be ignored
    _write_csv(f"{gdir}/mid_game.csv", ["section"], [{"section": "202"}])

    game = gms.load_game("cubs", "2026-06-14_dodgers_at_cubs")

    assert game["mid"] == [{"section": "202"}]


def test_load_game_complete_folder_loads_all_three_snapshots(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gdir = "data/mlb/cubs/2026-06-14_dodgers_at_cubs"
    os.makedirs(gdir)
    meta = {"home_team": "cubs", "opponent": "Dodgers", "game_date": "2026-06-14", "league": "mlb"}
    with open(f"{gdir}/game_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    _write_csv(f"{gdir}/pre_game.csv", ["section", "price_usd"], [{"section": "101", "price_usd": "89.00"}])
    _write_csv(f"{gdir}/mid_game.csv", ["section", "price_usd"], [{"section": "101", "price_usd": "89.00"}])
    _write_csv(f"{gdir}/no_shows.csv", ["section", "price_usd"], [{"section": "101", "price_usd": "89.00"}])

    game = gms.load_game("cubs", "2026-06-14_dodgers_at_cubs")

    assert len(game["pre"]) == 1
    assert len(game["mid"]) == 1
    assert len(game["noshows"]) == 1


# ── load_games_from_supabase() ────────────────────────────────────────────────

def _patch_supabase(monkeypatch, games_meta, pre_counts, mid_counts, no_shows_by_id):
    monkeypatch.setattr(
        gms.supabase_client, "fetch_games_for_team", lambda slug, league="mlb": games_meta
    )
    monkeypatch.setattr(
        gms.supabase_client,
        "count_listings",
        lambda game_id, snapshot: (pre_counts if snapshot == "pre_game" else mid_counts).get(game_id, 0),
    )
    monkeypatch.setattr(
        gms.supabase_client,
        "fetch_no_shows_for_game",
        lambda game_id: no_shows_by_id.get(game_id, []),
    )


def test_supabase_game_with_zero_pre_count_is_skipped(monkeypatch):
    games_meta = [{"id": 1, "game_date": "2026-06-14", "opponent": "Dodgers"}]
    _patch_supabase(monkeypatch, games_meta, pre_counts={}, mid_counts={}, no_shows_by_id={})

    assert gms.load_games_from_supabase("cubs") == []


def test_supabase_only_games_with_pre_count_are_included(monkeypatch):
    games_meta = [
        {"id": 1, "game_date": "2026-06-14", "opponent": "Dodgers"},
        {"id": 2, "game_date": "2026-06-21", "opponent": "Padres"},
    ]
    _patch_supabase(
        monkeypatch, games_meta,
        pre_counts={2: 100}, mid_counts={2: 80}, no_shows_by_id={},
    )

    result = gms.load_games_from_supabase("cubs")

    assert len(result) == 1
    assert result[0]["meta"]["opponent"] == "Padres"


def test_supabase_phantom_revenue_is_ticket_value_plus_concession_per_no_show(monkeypatch):
    games_meta = [{"id": 1, "game_date": "2026-06-14", "opponent": "Dodgers"}]
    no_shows = [{"price_usd": 100.0}, {"price_usd": 50.0}]
    _patch_supabase(
        monkeypatch, games_meta,
        pre_counts={1: 100}, mid_counts={1: 80}, no_shows_by_id={1: no_shows},
    )

    result = gms.load_games_from_supabase("cubs")

    # ns_value = 150, ns_count = 2 -> 150 + 2*35 = 220
    assert result[0]["phantom"] == 220.0


def test_supabase_no_show_with_none_price_usd_contributes_zero_not_a_crash(monkeypatch):
    # Supabase stores price_usd as NULL when the original price couldn't be
    # parsed (supabase_client.insert_listings' price_float fallback) -- the
    # sum here must treat that as 0, not raise on float(None).
    games_meta = [{"id": 1, "game_date": "2026-06-14", "opponent": "Dodgers"}]
    no_shows = [{"price_usd": None}, {"price_usd": 40.0}]
    _patch_supabase(
        monkeypatch, games_meta,
        pre_counts={1: 100}, mid_counts={1: 80}, no_shows_by_id={1: no_shows},
    )

    result = gms.load_games_from_supabase("cubs")

    # ns_value = 40, ns_count = 2 -> 40 + 2*35 = 110
    assert result[0]["phantom"] == 110.0


def test_supabase_meta_and_counts_are_mapped_from_the_row(monkeypatch):
    games_meta = [{
        "id": 1,
        "game_date": "2026-06-14",
        "opponent": "Dodgers",
        "arena": "Wrigley Field",
        "city": "Chicago",
        "home_team": "cubs",
        "day_of_week": "Sunday",
    }]
    no_shows = [{"price_usd": 75.0}]
    _patch_supabase(
        monkeypatch, games_meta,
        pre_counts={1: 200}, mid_counts={1: 150}, no_shows_by_id={1: no_shows},
    )

    result = gms.load_games_from_supabase("cubs")

    g = result[0]
    assert g["folder"] == "2026-06-14"
    assert g["meta"] == {
        "game_date": "2026-06-14",
        "opponent": "Dodgers",
        "arena": "Wrigley Field",
        "city": "Chicago",
        "home_team": "cubs",
        "league": "mlb",
        "day_of_week": "Sunday",
    }
    assert g["pre_count"] == 200
    assert g["mid_count"] == 150
    assert g["noshows"] == no_shows
    # The Supabase path never fetches full listing rows, only counts.
    assert g["pre"] == []
    assert g["mid"] == []


def test_supabase_missing_optional_fields_default_to_empty_string(monkeypatch):
    games_meta = [{"id": 1, "game_date": "2026-06-14"}]  # no arena/city/opponent/day_of_week
    _patch_supabase(
        monkeypatch, games_meta,
        pre_counts={1: 10}, mid_counts={1: 5}, no_shows_by_id={},
    )

    result = gms.load_games_from_supabase("cubs")

    meta = result[0]["meta"]
    assert meta["opponent"] == ""
    assert meta["arena"] == ""
    assert meta["city"] == ""
    assert meta["home_team"] == ""
    assert meta["day_of_week"] == ""
    assert meta["league"] == "mlb"


def test_supabase_multiple_games_preserve_order_and_each_get_own_phantom_value(monkeypatch):
    games_meta = [
        {"id": 1, "game_date": "2026-06-14", "opponent": "Dodgers"},
        {"id": 2, "game_date": "2026-06-21", "opponent": "Padres"},
    ]
    _patch_supabase(
        monkeypatch, games_meta,
        pre_counts={1: 100, 2: 50},
        mid_counts={1: 80, 2: 20},
        no_shows_by_id={1: [{"price_usd": 10.0}], 2: [{"price_usd": 20.0}, {"price_usd": 30.0}]},
    )

    result = gms.load_games_from_supabase("cubs")

    assert [g["folder"] for g in result] == ["2026-06-14", "2026-06-21"]
    assert result[0]["phantom"] == 10.0 + 1 * gms.CONCESSION_PER_SEAT
    assert result[1]["phantom"] == 50.0 + 2 * gms.CONCESSION_PER_SEAT


def test_supabase_no_games_at_all_returns_empty_list(monkeypatch):
    _patch_supabase(monkeypatch, [], pre_counts={}, mid_counts={}, no_shows_by_id={})

    assert gms.load_games_from_supabase("cubs") == []
