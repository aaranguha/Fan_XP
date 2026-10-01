"""
Tests for generate_team_story.py -- NBA's older, more complex generator that
combines the interactive seat visualization and dashboard content into one
page (docs/{team}_seat_story.html). CLAUDE.md §4.4 explicitly calls this out
as architecturally different from the other three leagues' generators (which
split "dashboard" and "interactive map" into separate pages) and says it was
"deliberately left alone" so the existing, working interactive visualization
wouldn't be disturbed. That history means it never got the same test
coverage generate_nfl_story.py/generate_mlb_story.py/generate_wnba_story.py/
generate_nba_dashboard.py did (all already covered elsewhere in this test
suite) -- this file closes that gap.

Covers the real pure-logic surface:
  - _rate_color(): the green->amber->red gradient driving the no-show-rate
    visuals.
  - dropdown_label()/story_label(): the per-game labels shown in the team
    page's dropdown and story text, including the documented
    "strip a parenthetical promo suffix off the opponent name" behavior and
    the folder-name-parsing fallback used when game_meta.json has no
    opponent recorded yet.
  - load_game_keys(): the per-game CSV loader, including its offer-level-CSV
    detection (break on the first row missing row/seat columns, same
    "no seat-level data" case documented in CLAUDE.md §4.1) and its
    malformed-price fallback.
  - find_all_games()/load_game_meta(): the game-folder discovery and
    game_meta.json loader.
  - build_games_data(): end-to-end per-game stats -- the phantom-revenue
    formula (dead seat value + no_show_count * $35 concession estimate,
    matching the formula CLAUDE.md §4.4 documents for the other leagues'
    generators, implemented separately here), the no-show rate, and the
    top_secs fallback to pre-game listings when a game has no no-shows yet.
"""

import csv
import json
import os

import pytest

import generate_team_story as gts


# ── _rate_color() ────────────────────────────────────────────────────────────

def test_rate_color_zero_is_pure_green():
    assert gts._rate_color(0.0) == "#00e5a0"


def test_rate_color_one_is_pure_red():
    assert gts._rate_color(1.0) == "#ff4d6d"


def test_rate_color_midpoint_is_amber():
    assert gts._rate_color(0.5) == "#ffd700"


def test_rate_color_negative_rate_clamps_to_zero():
    assert gts._rate_color(-5.0) == gts._rate_color(0.0)


def test_rate_color_above_one_clamps_to_one():
    assert gts._rate_color(5.0) == gts._rate_color(1.0)


# ── dropdown_label() ─────────────────────────────────────────────────────────

def test_dropdown_label_uses_opponent_last_word_and_formatted_date():
    label = gts.dropdown_label(
        "2026-04-07_sacramento_kings_at_warriors",
        {"opponent": "Los Angeles Lakers", "game_date": "2026-04-09"},
    )
    assert label == "4/9 · Lakers"


def test_dropdown_label_strips_parenthetical_promo_suffix_from_opponent():
    # Real TM event-name shape per the docstring example: a bobblehead-night
    # promo suffix appended in parens after the opponent name.
    label = gts.dropdown_label(
        "2026-04-07_x",
        {"opponent": "Charlotte Hornets (Jayson Tatum Bobblehead*)", "game_date": "2026-04-09"},
    )
    assert label == "4/9 · Hornets"


def test_dropdown_label_falls_back_to_folder_name_when_no_opponent():
    # No opponent recorded yet (e.g. game_meta.json not written) -- parse
    # the "<...>_at_<home>" folder-name convention instead.
    label = gts.dropdown_label("2026-04-07_sacramento_kings_at_warriors", {})
    assert label == "4/7 · Kings"


def test_dropdown_label_unparseable_folder_name_falls_back_to_question_mark():
    label = gts.dropdown_label("mystery_game_folder", {})
    assert label.endswith("· ?")


def test_dropdown_label_unparseable_date_falls_back_to_raw_date_string():
    label = gts.dropdown_label(
        "2026-04-07_x", {"opponent": "Lakers", "game_date": "not-a-date"}
    )
    assert label == "not-a-date · Lakers"


def test_dropdown_label_missing_date_falls_back_to_folder_prefix():
    label = gts.dropdown_label("2026-04-07_sacramento_kings_at_warriors", {"opponent": "Lakers"})
    assert label == "4/7 · Lakers"


# ── story_label() ────────────────────────────────────────────────────────────

def test_story_label_includes_vs_prefix_when_opponent_known():
    label = gts.story_label("2026-04-07_x", {"opponent": "Los Angeles Lakers", "game_date": "2026-04-09"})
    assert label == "vs. Los Angeles Lakers · Apr 9, 2026"


def test_story_label_omits_vs_prefix_when_opponent_unknown():
    label = gts.story_label("2026-04-07_x", {"game_date": "2026-04-09"})
    assert label == "Apr 9, 2026"


def test_story_label_missing_date_falls_back_to_folder_prefix():
    label = gts.story_label("2026-04-07_x", {})
    assert label == "Apr 7, 2026"


def test_story_label_unparseable_date_falls_back_to_raw_date_string():
    label = gts.story_label("2026-04-07_x", {"game_date": "bad-date"})
    assert label == "bad-date"


# ── load_game_keys() ─────────────────────────────────────────────────────────

def _write_csv(path, fieldnames, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_load_game_keys_parses_pre_game_and_no_shows(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gdir = "data/warriors/2026-04-07_a"
    os.makedirs(gdir)
    _write_csv(f"{gdir}/pre_game.csv", ["section", "row", "seat", "price_usd"], [
        {"section": "101", "row": "A", "seat": "1", "price_usd": "50.5"},
        {"section": "101", "row": "A", "seat": "2", "price_usd": "30"},
    ])
    _write_csv(f"{gdir}/no_shows.csv", ["section", "row", "seat"], [
        {"section": "101", "row": "A", "seat": "1"},
    ])

    ns_keys, pre_keys, pre_price = gts.load_game_keys(gdir)

    assert pre_keys == {("101", "A", "1"), ("101", "A", "2")}
    assert ns_keys == {("101", "A", "1")}
    assert pre_price[("101", "A", "1")] == 50  # rounded from 50.5... (round-half-to-even: 50.5 -> 50)
    assert pre_price[("101", "A", "2")] == 30


def test_load_game_keys_malformed_price_defaults_to_zero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gdir = "data/warriors/2026-04-07_a"
    os.makedirs(gdir)
    _write_csv(f"{gdir}/pre_game.csv", ["section", "row", "seat", "price_usd"], [
        {"section": "101", "row": "A", "seat": "1", "price_usd": "N/A"},
    ])

    _, pre_keys, pre_price = gts.load_game_keys(gdir)

    assert ("101", "A", "1") in pre_keys
    assert pre_price[("101", "A", "1")] == 0


def test_load_game_keys_offer_level_csv_has_no_seat_level_data(tmp_path, monkeypatch):
    # CLAUDE.md §4.1 documents offer-level CSVs (no row/seat columns, one row
    # per resale offer rather than per seat). load_game_keys() must detect
    # this on the first row and bail out to empty sets rather than crashing
    # on the missing "row"/"seat" keys or silently misreading other columns
    # as seat data.
    monkeypatch.chdir(tmp_path)
    gdir = "data/warriors/2026-04-07_offer"
    os.makedirs(gdir)
    _write_csv(f"{gdir}/pre_game.csv", ["section", "price_usd"], [
        {"section": "101", "price_usd": "50"},
    ])

    ns_keys, pre_keys, pre_price = gts.load_game_keys(gdir)

    assert pre_keys == set()
    assert ns_keys == set()
    assert pre_price == {}


def test_load_game_keys_missing_files_return_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gdir = "data/warriors/2026-04-07_missing"
    os.makedirs(gdir)

    ns_keys, pre_keys, pre_price = gts.load_game_keys(gdir)

    assert ns_keys == set()
    assert pre_keys == set()
    assert pre_price == {}


# ── load_game_meta() ─────────────────────────────────────────────────────────

def test_load_game_meta_reads_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gdir = "data/warriors/2026-04-07_a"
    os.makedirs(gdir)
    with open(f"{gdir}/game_meta.json", "w") as f:
        json.dump({"opponent": "Lakers", "game_date": "2026-04-07"}, f)

    meta = gts.load_game_meta(gdir)

    assert meta == {"opponent": "Lakers", "game_date": "2026-04-07"}


def test_load_game_meta_missing_file_returns_empty_dict(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gdir = "data/warriors/2026-04-07_a"
    os.makedirs(gdir)

    assert gts.load_game_meta(gdir) == {}


# ── find_all_games() ─────────────────────────────────────────────────────────

def test_find_all_games_only_includes_dated_folders_with_pre_game_csv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    team_dir = "data/warriors"
    os.makedirs(f"{team_dir}/2026-04-08_b")
    os.makedirs(f"{team_dir}/2026-04-07_a")
    os.makedirs(f"{team_dir}/not_a_game_dir")  # doesn't start with "202"
    _write_csv(f"{team_dir}/2026-04-08_b/pre_game.csv", ["section"], [{"section": "101"}])
    _write_csv(f"{team_dir}/2026-04-07_a/pre_game.csv", ["section"], [{"section": "101"}])
    # not_a_game_dir has no pre_game.csv at all

    games = gts.find_all_games("warriors")

    assert games == ["2026-04-07_a", "2026-04-08_b"]  # sorted oldest -> newest


def test_find_all_games_excludes_folder_without_pre_game_csv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    team_dir = "data/warriors"
    os.makedirs(f"{team_dir}/2026-04-07_no_csv")

    assert gts.find_all_games("warriors") == []


# ── build_games_data() ───────────────────────────────────────────────────────

def _setup_game(gdir, pre_rows, ns_rows=None, meta=None):
    os.makedirs(gdir, exist_ok=True)
    _write_csv(f"{gdir}/pre_game.csv", ["section", "row", "seat", "price_usd"], pre_rows)
    if ns_rows is not None:
        _write_csv(f"{gdir}/no_shows.csv", ["section", "row", "seat"], ns_rows)
    if meta is not None:
        with open(f"{gdir}/game_meta.json", "w") as f:
            json.dump(meta, f)


def test_build_games_data_computes_phantom_revenue_and_rate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup_game(
        "data/warriors/2026-04-07_a",
        pre_rows=[
            {"section": "101", "row": "A", "seat": "1", "price_usd": "50"},
            {"section": "101", "row": "A", "seat": "2", "price_usd": "30"},
            {"section": "205", "row": "B", "seat": "1", "price_usd": "20"},
        ],
        ns_rows=[{"section": "101", "row": "A", "seat": "1"}],
        meta={"opponent": "Los Angeles Lakers", "game_date": "2026-04-07"},
    )

    games = gts.build_games_data("warriors", ["2026-04-07_a"])

    assert len(games) == 1
    g = games[0]
    assert g["pre"] == 3
    assert g["ns"] == 1
    assert g["rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert g["dead"] == 50  # the no-show seat's listed price
    assert g["phantom"] == 85  # 50 (dead) + 1 * 35 (concession)
    assert g["label"] == "4/7 · Lakers"
    assert g["story"] == "vs. Los Angeles Lakers · Apr 7, 2026"


def test_build_games_data_skips_offer_level_games_with_no_seat_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup_game(
        "data/warriors/2026-04-07_offer",
        pre_rows=[{"section": "101", "row": "", "seat": "", "price_usd": "50"}],
    )
    # Overwrite with a true offer-level CSV (no row/seat columns at all).
    _write_csv(
        "data/warriors/2026-04-07_offer/pre_game.csv",
        ["section", "price_usd"],
        [{"section": "101", "price_usd": "50"}],
    )

    games = gts.build_games_data("warriors", ["2026-04-07_offer"])

    assert games == []


def test_build_games_data_top_secs_falls_back_to_pre_game_when_no_no_shows_yet(tmp_path, monkeypatch):
    # No no_shows.csv at all (halftime snapshot hasn't happened yet). The
    # top-sections chart must fall back to pre-game listing counts instead
    # of showing an empty chart.
    monkeypatch.chdir(tmp_path)
    _setup_game(
        "data/warriors/2026-04-08_b",
        pre_rows=[
            {"section": "101", "row": "A", "seat": "1", "price_usd": "10"},
            {"section": "205", "row": "B", "seat": "1", "price_usd": "10"},
            {"section": "205", "row": "B", "seat": "2", "price_usd": "10"},
        ],
    )

    games = gts.build_games_data("warriors", ["2026-04-08_b"])

    assert len(games) == 1
    g = games[0]
    assert g["ns"] == 0
    assert g["rate"] == 0
    assert g["phantom"] == 0
    assert g["topSecs"] == [("205", 2), ("101", 1)]  # sorted desc by pre-game count


def test_build_games_data_top_secs_sorted_by_no_show_count_and_capped_at_seven(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pre_rows = []
    ns_rows = []
    for i in range(10):
        sec = str(100 + i)
        pre_rows.append({"section": sec, "row": "A", "seat": "1", "price_usd": "10"})
        ns_rows.append({"section": sec, "row": "A", "seat": "1"})
    _setup_game("data/warriors/2026-04-07_c", pre_rows=pre_rows, ns_rows=ns_rows)

    games = gts.build_games_data("warriors", ["2026-04-07_c"])

    assert len(games[0]["topSecs"]) == 7


def test_build_games_data_empty_folder_list_returns_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert gts.build_games_data("warriors", []) == []
