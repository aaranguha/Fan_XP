"""
Tests for generate_nfl_story.py's local on-disk game loaders —
find_all_games(), load_csv_rows(), and load_game(). These read a team's
data/nfl/{slug}/ folders directly and are the fallback path generate_html()
falls back to (see generate_nfl_story.py's main(): `folders =
find_all_games(slug); games = [g for f in folders if (g := load_game(slug,
f))]`) when load_games_from_supabase() isn't used. They had zero direct
test coverage before this — only generate_team_story.py's separate NBA
versions of the same pattern were tested (test_generate_team_story.py).

The edge case worth protecting: a game directory can legitimately exist
with only pre_game.csv on disk (the runner has taken its pre-game baseline
but hasn't reached the halftime snapshot yet, or the plausibility gate in
nfl_run_game.py skipped the no-show insert — CLAUDE.md §4.2's
MIN_HALFTIME_RATIO gate). load_game() must load that partial folder without
crashing, returning empty lists for the snapshots that don't exist yet
rather than raising.
"""

import json
import os

import generate_nfl_story as gns


def _write_csv(path, header, rows):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# ── find_all_games() ─────────────────────────────────────────────────────────

def test_returns_empty_list_when_team_dir_does_not_exist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert gns.find_all_games("49ers") == []


def test_returns_empty_list_when_team_dir_exists_but_has_no_games(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("data/nfl/49ers")

    assert gns.find_all_games("49ers") == []


def test_only_folders_with_pre_game_csv_are_included(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # A real game folder.
    gdir = "data/nfl/49ers/2026-09-13_rams_at_49ers"
    os.makedirs(gdir)
    _write_csv(f"{gdir}/pre_game.csv", ["section"], [{"section": "101"}])
    # A folder that exists (e.g. created early by a crashed run) but never
    # actually got a pre-game scrape written to it.
    os.makedirs("data/nfl/49ers/2026-09-20_seahawks_at_49ers")
    # A stray non-directory file sitting in the team dir.
    with open("data/nfl/49ers/readme.txt", "w") as f:
        f.write("not a game folder")

    assert gns.find_all_games("49ers") == ["2026-09-13_rams_at_49ers"]


def test_results_are_sorted_regardless_of_creation_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for folder in ["2026-09-20_seahawks_at_49ers", "2026-09-06_saints_at_49ers", "2026-09-13_rams_at_49ers"]:
        gdir = f"data/nfl/49ers/{folder}"
        os.makedirs(gdir)
        _write_csv(f"{gdir}/pre_game.csv", ["section"], [{"section": "101"}])

    assert gns.find_all_games("49ers") == [
        "2026-09-06_saints_at_49ers",
        "2026-09-13_rams_at_49ers",
        "2026-09-20_seahawks_at_49ers",
    ]


# ── load_csv_rows() ──────────────────────────────────────────────────────────

def test_load_csv_rows_returns_empty_list_when_file_does_not_exist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert gns.load_csv_rows("data/nfl/49ers/nope/pre_game.csv") == []


def test_load_csv_rows_returns_empty_list_for_header_only_csv(tmp_path):
    path = tmp_path / "pre_game.csv"
    _write_csv(str(path), ["section", "row", "seat", "price_usd"], [])

    assert gns.load_csv_rows(str(path)) == []


def test_load_csv_rows_parses_rows_as_dicts(tmp_path):
    path = tmp_path / "pre_game.csv"
    _write_csv(
        str(path),
        ["section", "row", "seat", "price_usd"],
        [{"section": "101", "row": "5", "seat": "12", "price_usd": "89.00"}],
    )

    rows = gns.load_csv_rows(str(path))

    assert rows == [{"section": "101", "row": "5", "seat": "12", "price_usd": "89.00"}]


# ── load_game() ──────────────────────────────────────────────────────────────

def test_returns_none_when_game_meta_json_is_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gdir = "data/nfl/49ers/2026-09-13_rams_at_49ers"
    os.makedirs(gdir)
    _write_csv(f"{gdir}/pre_game.csv", ["section"], [{"section": "101"}])

    assert gns.load_game("49ers", "2026-09-13_rams_at_49ers") is None


def test_pre_game_only_folder_loads_with_empty_mid_and_noshows(tmp_path, monkeypatch):
    # The realistic in-progress state: pre-game snapshot taken, halftime
    # snapshot not yet reached. Must not raise.
    monkeypatch.chdir(tmp_path)
    gdir = "data/nfl/49ers/2026-09-13_rams_at_49ers"
    os.makedirs(gdir)
    meta = {"home_team": "49ers", "opponent": "Rams", "game_date": "2026-09-13", "league": "nfl"}
    with open(f"{gdir}/game_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    _write_csv(f"{gdir}/pre_game.csv", ["section", "price_usd"], [{"section": "101", "price_usd": "89.00"}])

    game = gns.load_game("49ers", "2026-09-13_rams_at_49ers")

    assert game["folder"] == "2026-09-13_rams_at_49ers"
    assert game["meta"] == meta
    assert game["pre"] == [{"section": "101", "price_usd": "89.00"}]
    assert game["mid"] == []
    assert game["noshows"] == []


def test_complete_game_folder_loads_all_three_snapshots(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gdir = "data/nfl/49ers/2026-09-13_rams_at_49ers"
    os.makedirs(gdir)
    meta = {"home_team": "49ers", "opponent": "Rams", "game_date": "2026-09-13", "league": "nfl"}
    with open(f"{gdir}/game_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    _write_csv(f"{gdir}/pre_game.csv", ["section", "price_usd"], [{"section": "101", "price_usd": "89.00"}])
    _write_csv(f"{gdir}/halftime.csv", ["section", "price_usd"], [{"section": "101", "price_usd": "89.00"}])
    _write_csv(f"{gdir}/no_shows.csv", ["section", "price_usd"], [{"section": "101", "price_usd": "89.00"}])

    game = gns.load_game("49ers", "2026-09-13_rams_at_49ers")

    assert len(game["pre"]) == 1
    assert len(game["mid"]) == 1
    assert len(game["noshows"]) == 1
