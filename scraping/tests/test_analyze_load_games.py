"""
Tests for analyze.py's load_games() — the filesystem walk that turns
data/{team}/{game_folder}/{pre_game,no_shows,game_meta}.* into the game
dicts avg_dead_inventory(), no_show_rate_by_section(), day_of_week_breakdown()
and opponent_strength_correlation() (see test_analyze.py) consume.

This is the one function in analyze.py with no prior coverage — everything
downstream of it already has tests that build game dicts by hand, but
nothing exercises the actual folder-walking, file-presence filtering, or the
meta-field defaulting/no-show-rate/dead-value computation that produces
those dicts in the first place. All tests build a throwaway data/ directory
under tmp_path and monkeypatch analyze.DATA_DIR at it — no real project data,
Supabase, or credentials are touched.
"""

import csv
import json
import os

import analyze


def _write_csv(path, rows):
    if not rows:
        # Still create the file so "exists but empty" is distinguishable
        # from "missing" in tests that need that distinction.
        open(path, "w", encoding="utf-8").close()
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _make_game_dir(base, team, folder, pre_rows=None, no_show_rows=None, meta=None,
                    skip_pre=False, skip_no_shows=False):
    gdir = os.path.join(base, team, folder)
    os.makedirs(gdir, exist_ok=True)
    if not skip_pre:
        _write_csv(os.path.join(gdir, "pre_game.csv"), pre_rows)
    if not skip_no_shows:
        _write_csv(os.path.join(gdir, "no_shows.csv"), no_show_rows)
    if meta is not None:
        with open(os.path.join(gdir, "game_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f)
    return gdir


def _one_pre_row(section="101", price="100"):
    return {"section": section, "row": "1", "seat": "1", "price_usd": price}


def test_complete_game_is_loaded_with_expected_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "DATA_DIR", str(tmp_path))
    _make_game_dir(
        tmp_path, "magic", "2026-03-11_cavaliers_at_magic",
        pre_rows=[_one_pre_row(price="100"), _one_pre_row(price="50")],
        no_show_rows=[_one_pre_row(price="100")],
        meta={"opponent": "Cavaliers", "game_date": "2026-03-11", "day_of_week": "Wednesday"},
    )

    games = analyze.load_games()

    assert len(games) == 1
    g = games[0]
    assert g["team"] == "magic"
    assert g["folder"] == "2026-03-11_cavaliers_at_magic"
    assert g["label"] == "Cavaliers @ Magic  (2026-03-11)"
    assert g["pre_count"] == 2
    assert g["no_show_count"] == 1
    assert g["no_show_rate"] == 0.5
    assert g["dead_value"] == 100.0
    assert g["avg_no_show_price"] == 100.0
    assert g["day_of_week"] == "Wednesday"
    assert g["opponent"] == "Cavaliers"


def test_folder_missing_pre_game_csv_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "DATA_DIR", str(tmp_path))
    _make_game_dir(
        tmp_path, "magic", "incomplete",
        no_show_rows=[_one_pre_row()], skip_pre=True,
    )

    assert analyze.load_games() == []


def test_folder_missing_no_shows_csv_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "DATA_DIR", str(tmp_path))
    _make_game_dir(
        tmp_path, "magic", "incomplete",
        pre_rows=[_one_pre_row()], skip_no_shows=True,
    )

    assert analyze.load_games() == []


def test_empty_pre_game_csv_is_skipped_not_a_zero_row_game(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "DATA_DIR", str(tmp_path))
    _make_game_dir(
        tmp_path, "magic", "empty_pre",
        pre_rows=[], no_show_rows=[_one_pre_row()],
    )

    assert analyze.load_games() == []


def test_empty_no_shows_csv_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "DATA_DIR", str(tmp_path))
    _make_game_dir(
        tmp_path, "magic", "empty_no_shows",
        pre_rows=[_one_pre_row()], no_show_rows=[],
    )

    assert analyze.load_games() == []


def test_missing_game_meta_json_defaults_label_and_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "DATA_DIR", str(tmp_path))
    _make_game_dir(
        tmp_path, "magic", "no_meta",
        pre_rows=[_one_pre_row()], no_show_rows=[_one_pre_row()],
    )

    games = analyze.load_games()

    assert len(games) == 1
    g = games[0]
    assert g["label"] == "? @ Magic  (?)"
    assert g["day_of_week"] == "Unknown"
    assert g["opponent"] == "Unknown"
    assert g["opponent_draw"] is None
    assert g["opponent_win_pct"] is None
    assert g["game_appeal"] is None


def test_no_show_rows_missing_price_usd_yield_zero_dead_value_and_avg(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "DATA_DIR", str(tmp_path))
    _make_game_dir(
        tmp_path, "magic", "no_prices",
        pre_rows=[_one_pre_row()],
        no_show_rows=[_one_pre_row(price=""), _one_pre_row(price="")],
    )

    games = analyze.load_games()

    assert games[0]["dead_value"] == 0
    assert games[0]["avg_no_show_price"] == 0


def test_dead_value_ignores_rows_with_missing_price_but_sums_the_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "DATA_DIR", str(tmp_path))
    _make_game_dir(
        tmp_path, "magic", "mixed_prices",
        pre_rows=[_one_pre_row()],
        no_show_rows=[_one_pre_row(price="100"), _one_pre_row(price="")],
    )

    games = analyze.load_games()

    assert games[0]["dead_value"] == 100.0
    assert games[0]["avg_no_show_price"] == 100.0


def test_stray_file_in_data_dir_is_not_treated_as_a_team(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "DATA_DIR", str(tmp_path))
    (tmp_path / "README.txt").write_text("not a team")
    _make_game_dir(
        tmp_path, "magic", "real_game",
        pre_rows=[_one_pre_row()], no_show_rows=[_one_pre_row()],
    )

    games = analyze.load_games()

    assert [g["team"] for g in games] == ["magic"]


def test_stray_file_inside_a_team_dir_is_not_treated_as_a_game_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "DATA_DIR", str(tmp_path))
    _make_game_dir(
        tmp_path, "magic", "real_game",
        pre_rows=[_one_pre_row()], no_show_rows=[_one_pre_row()],
    )
    (tmp_path / "magic" / "notes.txt").write_text("not a game folder")

    games = analyze.load_games()

    assert [g["folder"] for g in games] == ["real_game"]


def test_multiple_teams_and_games_are_all_loaded_in_sorted_order(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "DATA_DIR", str(tmp_path))
    _make_game_dir(tmp_path, "warriors", "2026-01-02_game",
                    pre_rows=[_one_pre_row()], no_show_rows=[_one_pre_row()])
    _make_game_dir(tmp_path, "magic", "2026-02-01_game",
                    pre_rows=[_one_pre_row()], no_show_rows=[_one_pre_row()])
    _make_game_dir(tmp_path, "magic", "2026-01-01_game",
                    pre_rows=[_one_pre_row()], no_show_rows=[_one_pre_row()])

    games = analyze.load_games()

    assert [(g["team"], g["folder"]) for g in games] == [
        ("magic", "2026-01-01_game"),
        ("magic", "2026-02-01_game"),
        ("warriors", "2026-01-02_game"),
    ]


def test_empty_data_dir_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "DATA_DIR", str(tmp_path))

    assert analyze.load_games() == []
