"""
Tests for extract_stadium_geo.py's "real data vs. honest preview" switch.

CLAUDE.md §4.4 documents this as a hard requirement, not a cosmetic detail:

    When a team has no real completed game yet, sections render in a
    clearly-labeled preview state -- a deterministic (seeded), honest
    spread of ~22% of lower-bowl sections marked "open" ... Never remove
    that labeling -- it's the line between an honest placeholder and a
    page that looks like it's showing real fan data when it isn't.

These tests cover the two functions/paths behind that switch:

- find_real_no_shows(): decides whether a team has real captured no-show
  data at all, which game it comes from, and builds the normalized
  (section, row, seat) set used to mark seats open for real.
- main(): the simulated/preview path (open_sections selection, and the
  invariant that only those sections can ever be marked open) and the
  real-data path (is_open exactly matches the real no-show set, and
  data_source/game_date are reported correctly either way).

A regression here would silently turn a fabricated preview into something
that *reads* as real captured attendance data on the live seat map, or vice
versa mislabel real data as a preview -- exactly the failure mode this file
exists to catch.
"""

import json
import random
import sys

import pytest

from extract_stadium_geo import find_real_no_shows, main, normalize


# ── find_real_no_shows() ──────────────────────────────────────────────────

def test_find_real_no_shows_returns_none_when_no_game_dir_exists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    game_date, seats = find_real_no_shows("niners")

    assert game_date is None
    assert seats is None


def test_find_real_no_shows_picks_the_chronologically_latest_game_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    early = tmp_path / "data" / "nfl" / "niners" / "2026-09-13_week1"
    late = tmp_path / "data" / "nfl" / "niners" / "2026-10-05_week5"
    early.mkdir(parents=True)
    late.mkdir(parents=True)
    (early / "no_shows.csv").write_text("section,row,seat\n101,A,1\n")
    (late / "no_shows.csv").write_text("section,row,seat\n105,B,2\n")

    game_date, seats = find_real_no_shows("niners")

    assert game_date == "2026-10-05"
    assert seats == {("105", "B", "2")}


def test_find_real_no_shows_normalizes_section_row_seat(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    game_dir = tmp_path / "data" / "nfl" / "niners" / "2026-10-05_week5"
    game_dir.mkdir(parents=True)
    (game_dir / "no_shows.csv").write_text(
        "section,row,seat\n  101 , row a ,  7 \n"
    )

    _, seats = find_real_no_shows("niners")

    assert seats == {(normalize("101"), normalize("row a"), normalize("7"))}
    assert seats == {("101", "ROW A", "7")}


# ── main(): shared fixture builders ─────────────────────────────────────

def _place(seat_num, x, y):
    # [unused_key, seat_num, x, y] -- the 4-element minimum main() reads.
    return [None, seat_num, x, y]


def _section(name, seat_coords):
    return {
        "segmentCategory": "SECTION",
        "name": name,
        "segments": [
            {"name": "A", "placesNoKeys": [_place(str(i + 1), x, y) for i, (x, y) in enumerate(seat_coords)]}
        ],
    }


def _write_geo_json(base_dir, slug, section_defs):
    # Eight seats per section spread on a line, so each section always has
    # a convex hull degenerate case main() knows how to handle (see the
    # len(hull) < 3 branch) without needing real 2D spread for this test.
    geo = {
        "pages": [
            {
                "segments": [
                    _section(name, [(float(i), 0.0) for i in range(8)])
                    for name in section_defs
                ]
            }
        ]
    }
    team_dir = base_dir / "data" / slug
    team_dir.mkdir(parents=True)
    (team_dir / "seatmap_geo.json").write_text(json.dumps(geo))
    return team_dir


def _run_main(monkeypatch, slug):
    monkeypatch.setattr(sys, "argv", ["extract_stadium_geo.py", slug])
    main()


# ── main(): preview (simulated) path ────────────────────────────────────

def test_preview_path_marks_data_source_simulated_with_no_game_date(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    slug = "niners"
    # 8 lower-tier sections -> open_sections = every 4th of the sorted
    # names, capped at 14: lower_named[::4][:14] = {"101", "105"}.
    lower_sections = [f"10{i}" for i in range(1, 9)]  # 101..108
    _write_geo_json(tmp_path, slug, lower_sections)
    random.seed(49)

    _run_main(monkeypatch, slug)

    out = json.loads((tmp_path / "data" / slug / "seatmap_extract.json").read_text())
    assert out["data_source"] == "simulated"
    assert out["game_date"] is None


def test_preview_path_only_marks_seats_open_inside_the_selected_open_sections(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    slug = "niners"
    lower_sections = [f"10{i}" for i in range(1, 9)]  # 101..108
    _write_geo_json(tmp_path, slug, lower_sections)
    random.seed(49)

    _run_main(monkeypatch, slug)

    out = json.loads((tmp_path / "data" / slug / "seatmap_extract.json").read_text())
    expected_open_sections = {"101", "105"}  # lower_named[::4][:14]

    for name, dots in out["secs"].items():
        any_open = any(dot[5] for dot in dots)
        if any_open:
            assert name in expected_open_sections, (
                f"section {name} was marked open but is not in the deterministic "
                "open_sections spread -- preview is no longer honest about which "
                "seats it fabricates"
            )


def test_preview_path_never_marks_non_lower_sections_open(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    slug = "niners"
    # A mezzanine (200s), an upper (300s) and a club section alongside the
    # lower bowl -- none of these are eligible for the preview spread at all.
    sections = ["101", "102", "103", "104", "105", "250", "350", "C101"]
    _write_geo_json(tmp_path, slug, sections)
    random.seed(49)

    _run_main(monkeypatch, slug)

    out = json.loads((tmp_path / "data" / slug / "seatmap_extract.json").read_text())
    for name in ("250", "350", "C101"):
        assert all(dot[5] is False for dot in out["secs"][name])


# ── main(): real no-show data path ──────────────────────────────────────

def test_real_data_path_reports_real_source_and_game_date(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    slug = "niners"
    _write_geo_json(tmp_path, slug, ["101", "102"])
    game_dir = tmp_path / "data" / "nfl" / slug / "2026-10-05_week5"
    game_dir.mkdir(parents=True)
    (game_dir / "no_shows.csv").write_text("section,row,seat\n101,A,1\n")

    _run_main(monkeypatch, slug)

    out = json.loads((tmp_path / "data" / slug / "seatmap_extract.json").read_text())
    assert out["data_source"] == "real"
    assert out["game_date"] == "2026-10-05"


def test_real_data_path_open_flags_exactly_match_the_no_show_set(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    slug = "niners"
    _write_geo_json(tmp_path, slug, ["101", "102"])
    game_dir = tmp_path / "data" / "nfl" / slug / "2026-10-05_week5"
    game_dir.mkdir(parents=True)
    # Seat "1" (row A) of section 101 is a confirmed no-show; nothing else is.
    (game_dir / "no_shows.csv").write_text("section,row,seat\n101,A,1\n")

    _run_main(monkeypatch, slug)

    out = json.loads((tmp_path / "data" / slug / "seatmap_extract.json").read_text())
    sec_101_dots = out["secs"]["101"]
    # dot layout: [x, y, row_label, seat_num, level, is_open]
    open_seats = {(d[2], d[3]) for d in sec_101_dots if d[5]}
    assert open_seats == {("A", "1")}
    # Nothing in section 102 is a no-show, so every seat there stays closed,
    # even though real data is in play (no leftover preview randomness).
    assert all(dot[5] is False for dot in out["secs"]["102"])


def test_real_data_path_takes_precedence_even_with_no_real_no_shows_recorded(tmp_path, monkeypatch):
    # A team can have a completed home game where literally nobody no-showed
    # (no_shows.csv exists but is empty of data rows) -- that must still be
    # reported as real, zero-open data, never silently fall back to the
    # fabricated preview spread.
    monkeypatch.chdir(tmp_path)
    slug = "niners"
    lower_sections = [f"10{i}" for i in range(1, 9)]
    _write_geo_json(tmp_path, slug, lower_sections)
    game_dir = tmp_path / "data" / "nfl" / slug / "2026-10-05_week5"
    game_dir.mkdir(parents=True)
    (game_dir / "no_shows.csv").write_text("section,row,seat\n")

    _run_main(monkeypatch, slug)

    out = json.loads((tmp_path / "data" / slug / "seatmap_extract.json").read_text())
    assert out["data_source"] == "real"
    assert out["game_date"] == "2026-10-05"
    for dots in out["secs"].values():
        assert all(dot[5] is False for dot in dots)


# ── main(): missing input file ──────────────────────────────────────────

def test_main_exits_cleanly_when_seatmap_geo_json_is_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["extract_stadium_geo.py", "no-such-team"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
