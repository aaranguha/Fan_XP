"""
Tests for build_stadium_seatmap.py -- the generator for the interactive NFL
seat-map page (docs/nfl_{slug}_seatmap.html), the "permanent architecture"
CLAUDE.md §4.3 describes for drawing sections from real seat geometry.

Three things covered here, none touched by existing extract_stadium_geo.py
tests (which cover the upstream geometry math, not this downstream renderer):

- darken(): the hex-color helper used to derive the page's --team-dark
  token from each team's brand color.
- main()'s viewBox-fitting math: a comment in this file notes that
  different venues' seating bowls have genuinely different real-world
  aspect ratios (Lambeau's bowl is closer to portrait than Levi's
  Stadium's), but the page's map area is always a landscape box -- so
  main() pads the tight-fit bounding box out to a fixed TARGET_ASPECT
  regardless of which axis was tight. A regression here would letterbox
  portrait-shaped venues instead of filling the map area.
- main()'s team_name source: a comment in the file flags that
  slug.title() mangles "49ers" into "49Ers" -- main() deliberately uses
  NFL_TEAMS[slug]["tm_keyword"] instead. These tests use the real "49ers"
  slug specifically because it is the one case that would catch a
  regression back to slug.title().
- main()'s real-vs-preview copy switch (subline/cart_note/legend label),
  the renderer-side half of the honest-preview-labeling requirement
  CLAUDE.md §4.4 calls out as something to never remove.
"""

import json
import re
import sys

import pytest

from build_stadium_seatmap import darken, main


# ── darken() ───────────────────────────────────────────────────────────────

def test_darken_applies_default_factor_and_truncates():
    # int(255 * 0.55) == 140 == 0x8c
    assert darken("#ff0000") == "#8c0000"


def test_darken_accepts_hash_prefixed_and_bare_hex_identically():
    assert darken("#336699") == darken("336699") == "#1c3854"


def test_darken_custom_factor():
    assert darken("#64c8ff", factor=0.5) == "#32647f"


def test_darken_never_goes_negative_or_overflows():
    assert darken("#000000") == "#000000"
    assert darken("#ffffff", factor=1.0) == "#ffffff"


# ── main(): shared fixture builder ─────────────────────────────────────────

def _dot(x, y, row="A", seat="1", level="lower", is_open=False):
    return [x, y, row, seat, level, is_open]


def _write_inputs(tmp_path, slug, secs, data_source="real", game_date="2026-10-05"):
    scraping_dir = tmp_path / "scraping"
    team_dir = scraping_dir / "data" / slug
    team_dir.mkdir(parents=True)

    extract = {
        "secs": secs,
        "tiers": {},
        "centroids": {},
        "hulls": {},
        "data_source": data_source,
        "game_date": game_date,
    }
    (team_dir / "seatmap_extract.json").write_text(json.dumps(extract))
    (team_dir / "arena_full.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><g id="arena"></g></svg>'
    )
    return scraping_dir


def _run_main(monkeypatch, tmp_path, slug, secs, **extract_kwargs):
    scraping_dir = _write_inputs(tmp_path, slug, secs, **extract_kwargs)
    monkeypatch.chdir(scraping_dir)
    monkeypatch.setattr(sys, "argv", ["build_stadium_seatmap.py", slug])
    main()
    return (tmp_path / "docs" / f"nfl_{slug}_seatmap.html").read_text()


def _viewbox_wh(html):
    m = re.search(r'viewBox="([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+)"', html)
    assert m, "no viewBox found in rendered seatmap HTML"
    return float(m.group(3)), float(m.group(4))


TARGET_ASPECT = 10240 / 7680


# ── main(): viewBox aspect-ratio fitting ───────────────────────────────────

def test_viewbox_is_padded_to_target_aspect_for_a_portrait_bowl(monkeypatch, tmp_path):
    # Narrow and tall (x spans 100, y spans 500) -- w/h starts well under
    # TARGET_ASPECT, so main() must widen vb_w to compensate.
    secs = {"101": [_dot(0, 0), _dot(100, 500)]}
    html = _run_main(monkeypatch, tmp_path, "49ers", secs)

    w, h = _viewbox_wh(html)
    assert w / h == pytest.approx(TARGET_ASPECT, rel=1e-3)


def test_viewbox_is_padded_to_target_aspect_for_a_wide_bowl(monkeypatch, tmp_path):
    # Wide and short (x spans 2000, y spans 100) -- w/h starts well over
    # TARGET_ASPECT, so main() must grow vb_h to compensate instead.
    secs = {"101": [_dot(0, 0), _dot(2000, 100)]}
    html = _run_main(monkeypatch, tmp_path, "49ers", secs)

    w, h = _viewbox_wh(html)
    assert w / h == pytest.approx(TARGET_ASPECT, rel=1e-3)


def test_viewbox_centered_on_the_padded_content_bbox(monkeypatch, tmp_path):
    secs = {"101": [_dot(0, 0), _dot(100, 100)]}
    html = _run_main(monkeypatch, tmp_path, "49ers", secs)

    m = re.search(r'viewBox="([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+)"', html)
    vb_x, vb_y, vb_w, vb_h = (float(g) for g in m.groups())
    # Content (with its own 16% pad) is centered at (50, 50); the aspect-fit
    # step must re-center around that same point, not the original corner.
    assert vb_x + vb_w / 2 == pytest.approx(50, abs=1)
    assert vb_y + vb_h / 2 == pytest.approx(50, abs=1)


# ── main(): team_name uses tm_keyword, not slug.title() ────────────────────

def test_team_name_uses_tm_keyword_not_titlecased_slug(monkeypatch, tmp_path):
    secs = {"101": [_dot(0, 0), _dot(100, 100)]}
    html = _run_main(monkeypatch, tmp_path, "49ers", secs)

    assert "San Francisco 49ers" in html
    assert "49Ers" not in html


# ── main(): real vs. preview copy switch ────────────────────────────────────

def test_real_data_source_uses_confirmed_no_show_copy(monkeypatch, tmp_path):
    secs = {"101": [_dot(0, 0), _dot(100, 100)]}
    html = _run_main(monkeypatch, tmp_path, "49ers", secs, data_source="real", game_date="2026-10-05")

    assert "Confirmed empty seats from the 2026-10-05 game" in html
    assert "Empty &mdash; confirmed no-show" in html
    assert "illustrative open seats" not in html


def test_simulated_data_source_uses_honest_preview_copy(monkeypatch, tmp_path):
    secs = {"101": [_dot(0, 0), _dot(100, 100)]}
    html = _run_main(monkeypatch, tmp_path, "49ers", secs, data_source="simulated", game_date=None)

    assert "illustrative open seats, not confirmed no-shows" in html
    assert "Empty &mdash; preview only" in html
    assert "availability shown is a preview" in html
    assert "Confirmed empty seats" not in html


# ── main(): unknown slug / missing input files ─────────────────────────────

def test_main_exits_cleanly_for_unknown_slug(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["build_stadium_seatmap.py", "not-a-real-team"])

    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1


def test_main_exits_cleanly_when_input_files_are_missing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["build_stadium_seatmap.py", "49ers"])

    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1
