"""
Tests for backfill_supabase.py's pure filesystem/CSV helpers.

This is the one-time script used to load historical NBA CSV data into
Supabase (CLAUDE.md's "do not assume historical data has been cleaned"
sibling script — it is the thing that originally populated the tables the
duplicate-insert bug later corrupted). `load_csv()` and `find_game_folders()`
are pure filesystem logic with no network/Supabase dependency and were
previously untested.

Covers:
  - load_csv(): missing file, header-only file, and a normal file all
    return the right list-of-dicts shape (or [] without raising).
  - find_game_folders(): walks DATA_ROOT/<team>/<game_folder>/ and returns
    (team_slug, game_path) only for folders that actually have a
    game_meta.json, while skipping the dedicated "mlb" team_slug (handled by
    a separate pipeline per the script's own docstring), non-directory
    entries at either level, and game folders missing the meta file —
    and returns them sorted by team then game folder.
"""

import os

import backfill_supabase
from backfill_supabase import load_csv, find_game_folders


# ── load_csv() ───────────────────────────────────────────────────────────

def test_load_csv_missing_file_returns_empty_list(tmp_path):
    assert load_csv(str(tmp_path / "does_not_exist.csv")) == []


def test_load_csv_reads_rows_as_dicts(tmp_path):
    path = tmp_path / "pre_game.csv"
    path.write_text("section,row,seat,price_usd\n101,5,10,150\n102,3,2,90\n")

    rows = load_csv(str(path))

    assert rows == [
        {"section": "101", "row": "5", "seat": "10", "price_usd": "150"},
        {"section": "102", "row": "3", "seat": "2", "price_usd": "90"},
    ]


def test_load_csv_header_only_file_returns_empty_list(tmp_path):
    path = tmp_path / "no_shows.csv"
    path.write_text("section,row,seat\n")

    assert load_csv(str(path)) == []


# ── find_game_folders() ──────────────────────────────────────────────────

def _make_game(data_root, team_slug, game_folder, with_meta=True):
    game_path = data_root / team_slug / game_folder
    game_path.mkdir(parents=True)
    if with_meta:
        (game_path / "game_meta.json").write_text("{}")
    return game_path


def test_finds_a_single_valid_game_folder(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    _make_game(data_root, "warriors", "2026-01-15_lakers")
    monkeypatch.setattr(backfill_supabase, "DATA_ROOT", str(data_root))

    results = find_game_folders()

    assert results == [("warriors", os.path.join(str(data_root), "warriors", "2026-01-15_lakers"))]


def test_skips_mlb_team_slug_entirely(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    _make_game(data_root, "mlb", "2026-04-01_yankees")
    _make_game(data_root, "warriors", "2026-01-15_lakers")
    monkeypatch.setattr(backfill_supabase, "DATA_ROOT", str(data_root))

    results = find_game_folders()

    assert [team for team, _ in results] == ["warriors"]


def test_skips_game_folder_missing_game_meta_json(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    _make_game(data_root, "warriors", "2026-01-15_lakers", with_meta=False)
    monkeypatch.setattr(backfill_supabase, "DATA_ROOT", str(data_root))

    assert find_game_folders() == []


def test_skips_non_directory_entry_at_team_level(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "README.txt").write_text("not a team")
    _make_game(data_root, "warriors", "2026-01-15_lakers")
    monkeypatch.setattr(backfill_supabase, "DATA_ROOT", str(data_root))

    results = find_game_folders()

    assert [team for team, _ in results] == ["warriors"]


def test_skips_non_directory_entry_at_game_level(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    team_path = data_root / "warriors"
    team_path.mkdir(parents=True)
    (team_path / "notes.txt").write_text("stray file, not a game folder")
    _make_game(data_root, "warriors", "2026-01-15_lakers")
    monkeypatch.setattr(backfill_supabase, "DATA_ROOT", str(data_root))

    results = find_game_folders()

    assert [folder for _, folder in results] == [
        os.path.join(str(data_root), "warriors", "2026-01-15_lakers")
    ]


def test_multiple_teams_and_games_all_returned_sorted(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    _make_game(data_root, "warriors", "2026-01-20_nets")
    _make_game(data_root, "warriors", "2026-01-15_lakers")
    _make_game(data_root, "bulls", "2026-01-10_knicks")
    monkeypatch.setattr(backfill_supabase, "DATA_ROOT", str(data_root))

    results = find_game_folders()

    assert results == [
        ("bulls", os.path.join(str(data_root), "bulls", "2026-01-10_knicks")),
        ("warriors", os.path.join(str(data_root), "warriors", "2026-01-15_lakers")),
        ("warriors", os.path.join(str(data_root), "warriors", "2026-01-20_nets")),
    ]


def test_empty_data_root_returns_empty_list(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr(backfill_supabase, "DATA_ROOT", str(data_root))

    assert find_game_folders() == []
