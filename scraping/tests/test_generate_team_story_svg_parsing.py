"""
Tests for generate_team_story.py's SVG/geometry parsing helpers --
load_section_paths(), _find_group_end(), load_bg_parts(), and
load_geo_multi(). test_generate_team_story.py (PR #34) already covers this
module's per-game stats/label helpers but explicitly left this lower-level
SVG-parsing surface untested; this file closes that gap.

These four functions are how the NBA seat-story generator (CLAUDE.md §4.4:
"deliberately left alone" so its existing, working interactive visualization
wouldn't be disturbed) turns Ticketmaster's raw geometry capture --
arena.svg (DOM section hit areas), arena_full.svg (background art), and
seatmap_geo.json (per-seat coordinates) -- into the arena map every
{team}_seat_story.html page draws.

Covers:
  - load_section_paths(): extracts {section_name: svg_path_d} from the
    DOM-extracted SVG, skipping path elements that lack either attribute,
    and returning {} for a missing/None file.
  - _find_group_end(): the nested-<g>-depth-counter used to carve the
    court/field group out of the background SVG. Includes a regression test
    for its working case (start at the very beginning of the group) and an
    xfail documenting a real bug when the group doesn't start at position 0
    (see the companion GitHub issue -- not fixed here per QA-agent policy).
  - load_bg_parts(): XML/svg-wrapper stripping, the bg_svg -> fallback_svg
    failover, and the <image> y-attribute injection (computed from the
    field group's own path commands when the image tag doesn't already
    carry one).
  - load_geo_multi(): walks seatmap_geo.json's COMPOSITE/SECTION/ROW
    segment tree (real shape confirmed against scraping/data/blazers/
    seatmap_geo.json) into per-section seat-dot lists, tagging each dot as
    "matched a pre-game key" (with its price) or not.
"""

import json

import pytest

import generate_team_story as gts


# ── load_section_paths() ─────────────────────────────────────────────────────

def test_load_section_paths_extracts_name_to_path_mapping(tmp_path):
    svg = (
        '<svg>'
        '<path data-section-name="101" d="M0,0 L10,10"/>'
        '<path data-section-name="102" d="M5,5 L15,15"/>'
        '</svg>'
    )
    f = tmp_path / "arena.svg"
    f.write_text(svg)

    assert gts.load_section_paths(str(f)) == {
        "101": "M0,0 L10,10",
        "102": "M5,5 L15,15",
    }


def test_load_section_paths_skips_path_without_d_attribute(tmp_path):
    svg = (
        '<svg>'
        '<path data-section-name="101" d="M0,0 L10,10"/>'
        '<path data-section-name="102"/>'
        '</svg>'
    )
    f = tmp_path / "arena.svg"
    f.write_text(svg)

    assert gts.load_section_paths(str(f)) == {"101": "M0,0 L10,10"}


def test_load_section_paths_missing_file_returns_empty_dict(tmp_path):
    assert gts.load_section_paths(str(tmp_path / "nope.svg")) == {}


def test_load_section_paths_none_path_returns_empty_dict():
    assert gts.load_section_paths(None) == {}


# ── _find_group_end() ────────────────────────────────────────────────────────

def test_find_group_end_at_position_zero_with_nested_group():
    content = '<g id="outer"><g id="inner"><rect/></g><circle/></g><g id="next">X</g>'

    end = gts._find_group_end(content, 0)

    assert content[:end] == '<g id="outer"><g id="inner"><rect/></g><circle/></g>'
    assert content[end:] == '<g id="next">X</g>'


def test_find_group_end_with_two_sibling_nested_groups():
    content = '<g id="outer"><g id="a"></g><g id="b"></g></g>TAIL'

    end = gts._find_group_end(content, 0)

    assert content[:end] == '<g id="outer"><g id="a"></g><g id="b"></g></g>'
    assert content[end:] == "TAIL"


def test_find_group_end_with_no_closing_tag_returns_full_length():
    content = '<g id="unclosed"><rect/>'

    assert gts._find_group_end(content, 0) == len(content)


@pytest.mark.xfail(
    reason=(
        "Real, live bug in _find_group_end(): it computes the opening "
        "tag's end as `start + content.index('>', start)`, but "
        "content.index(..., start) already returns an ABSOLUTE index (the "
        "search starts at `start`, but the match position returned is "
        "relative to the whole string, not to `start`). Adding `start` "
        "again double-counts it, so for any group that doesn't begin at "
        "position 0 in `content`, depth-tracking starts from the wrong "
        "offset and the function returns an end position that cuts the "
        "group off early. load_bg_parts() calls this with "
        "`start = inner.find('<g id=\"field\">')`, which is only 0 by "
        "coincidence in today's data (no NBA arena_full.svg currently has "
        "other content before the field group) -- the moment any arena "
        "SVG puts something before it, load_bg_parts() will silently "
        "truncate the court/field group. Not fixed here per the QA-agent "
        "policy of not patching bugs it finds; see the companion GitHub "
        "issue."
    ),
    strict=True,
)
def test_find_group_end_correct_behavior_when_group_is_not_at_position_zero():
    """
    Real, live bug in _find_group_end(): it computes the opening tag's end
    as `start + content.index('>', start)`, but content.index(..., start)
    already returns an ABSOLUTE index (the search starts at `start`, but the
    match position returned is relative to the whole string, not to
    `start`). Adding `start` again double-counts it, so for any group that
    doesn't begin at position 0 in `content`, depth-tracking starts from the
    wrong offset and the function returns an end position that cuts the
    group off early -- confirmed live: for '<g id="a"><g id="b"></g></g>'
    preceded by a 3-character prefix, this returns an end that drops the
    outer group's own closing '</g>', leaving it dangling in the "remainder"
    instead of inside the extracted group.

    load_bg_parts() calls this with `start = inner.find('<g id="field">')`,
    which is only 0 by coincidence in today's data (no NBA arena_full.svg
    currently has other content before the field group) -- the moment any
    arena SVG puts something before it, load_bg_parts() will silently
    truncate the court/field group. Not fixed here per the QA-agent policy
    of not patching bugs it finds; see the companion GitHub issue.
    """
    content = 'PAD<g id="a"><g id="b"></g></g>TAIL'
    start = content.index('<g id="a"')

    end = gts._find_group_end(content, start)

    assert content[start:end] == '<g id="a"><g id="b"></g></g>'
    assert content[end:] == "TAIL"


# ── load_bg_parts() ──────────────────────────────────────────────────────────

def test_load_bg_parts_missing_file_and_no_fallback_returns_empty_strings(tmp_path):
    inner, court = gts.load_bg_parts(str(tmp_path / "missing.svg"))

    assert (inner, court) == ("", "")


def test_load_bg_parts_falls_back_when_primary_file_missing(tmp_path):
    fallback = tmp_path / "fallback.svg"
    fallback.write_text('<?xml version="1.0"?><svg width="1" height="1"><g id="other">Y</g></svg>')

    inner, court = gts.load_bg_parts(str(tmp_path / "missing.svg"), fallback_svg=str(fallback))

    assert inner == '<g id="other">Y</g>'
    assert court == ""


def test_load_bg_parts_strips_xml_declaration_and_svg_wrapper(tmp_path):
    f = tmp_path / "bg.svg"
    f.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<svg width="10" height="10"><g id="other">Z</g></svg>')

    inner, court = gts.load_bg_parts(str(f))

    assert inner == '<g id="other">Z</g>'
    assert court == ""


def test_load_bg_parts_extracts_field_group_and_removes_it_from_inner(tmp_path):
    f = tmp_path / "bg.svg"
    f.write_text(
        '<?xml version="1.0"?><svg width="10" height="10">'
        '<g id="field"><path d="M1,1 L2,2"/></g>'
        '<g id="other">Z</g></svg>'
    )

    inner, court = gts.load_bg_parts(str(f))

    assert inner == '<g id="other">Z</g>'
    assert court == '<g id="field"><path d="M1,1 L2,2"/></g>'


def test_load_bg_parts_injects_missing_image_y_from_field_path_commands(tmp_path):
    f = tmp_path / "bg.svg"
    f.write_text(
        '<?xml version="1.0"?><svg width="10" height="10">'
        '<g id="field"><path d="M10.0,20.5 L30,40"/><image x="0" width="50" height="50"/></g>'
        '</svg>'
    )

    _inner, court = gts.load_bg_parts(str(f))

    assert '<image y="20.50" x="0" width="50" height="50"/>' in court


def test_load_bg_parts_leaves_existing_image_y_untouched(tmp_path):
    f = tmp_path / "bg.svg"
    f.write_text(
        '<?xml version="1.0"?><svg width="10" height="10">'
        '<g id="field"><path d="M5,5 L9,9"/><image x="0" y="99" width="50" height="50"/></g>'
        '</svg>'
    )

    _inner, court = gts.load_bg_parts(str(f))

    assert '<image x="0" y="99" width="50" height="50"/>' in court
    assert 'y="5' not in court


def test_load_bg_parts_no_field_group_leaves_inner_untouched_and_court_empty(tmp_path):
    f = tmp_path / "bg.svg"
    f.write_text('<?xml version="1.0"?><svg width="10" height="10"><g id="other">Y</g></svg>')

    inner, court = gts.load_bg_parts(str(f))

    assert inner == '<g id="other">Y</g>'
    assert court == ""


# ── load_geo_multi() ──────────────────────────────────────────────────────────

def _composite_section(name, rows):
    """Build one top-level COMPOSITE segment shaped like a real TM capture
    (confirmed against scraping/data/blazers/seatmap_geo.json): COMPOSITE ->
    one SECTION child -> ROW children, each carrying placesNoKeys tuples of
    (place_id, seat_num, x, y, row_label, placeSize, status)."""
    return {
        "name": name,
        "segmentCategory": "COMPOSITE",
        "segments": [{
            "name": name,
            "segmentCategory": "SECTION",
            "segments": [
                {
                    "name": row_name,
                    "segmentCategory": "ROW",
                    "placesNoKeys": places,
                }
                for row_name, places in rows
            ],
        }],
    }


def test_load_geo_multi_tags_seats_matched_against_pre_game_keys(tmp_path):
    geo = {
        "pages": [{
            "segments": [
                _composite_section("115", [
                    ("C", [["p1", "1", 100.0, 200.0], ["p2", "2", 110.0, 210.0]]),
                ]),
            ],
        }],
    }
    f = tmp_path / "seatmap_geo.json"
    f.write_text(json.dumps(geo))

    all_pre_keys = {("115", "C", "1")}
    all_pre_price = {("115", "C", "1"): 42.5}

    sections = gts.load_geo_multi(str(f), all_pre_keys, all_pre_price)

    sx, sy = gts.SX, gts.SY
    assert sections["115"] == [
        [round(100.0 * sx, 2), round(200.0 * sy, 2), 1, "C", "1", 42.5],
        [round(110.0 * sx, 2), round(210.0 * sy, 2), 0],
    ]


def test_load_geo_multi_combines_multiple_rows_into_one_section(tmp_path):
    geo = {
        "pages": [{
            "segments": [
                _composite_section("200", [
                    ("A", [["p1", "1", 0.0, 0.0]]),
                    ("B", [["p2", "1", 10.0, 10.0]]),
                ]),
            ],
        }],
    }
    f = tmp_path / "seatmap_geo.json"
    f.write_text(json.dumps(geo))

    all_pre_keys = {("200", "A", "1"), ("200", "B", "1")}
    sections = gts.load_geo_multi(str(f), all_pre_keys, {})

    assert len(sections["200"]) == 2
    assert {dot[3] for dot in sections["200"]} == {"A", "B"}


def test_load_geo_multi_section_with_no_seats_is_omitted(tmp_path):
    geo = {
        "pages": [{
            "segments": [
                _composite_section("300", [("A", [])]),
            ],
        }],
    }
    f = tmp_path / "seatmap_geo.json"
    f.write_text(json.dumps(geo))

    sections = gts.load_geo_multi(str(f), set(), {})

    assert "300" not in sections


def test_load_geo_multi_handles_document_without_pages_wrapper(tmp_path):
    geo = {
        "segments": [
            _composite_section("400", [("A", [["p1", "1", 1.0, 1.0]])]),
        ],
    }
    f = tmp_path / "seatmap_geo.json"
    f.write_text(json.dumps(geo))

    sections = gts.load_geo_multi(str(f), set(), {})

    assert "400" in sections


def test_load_geo_multi_non_composite_top_segment_without_composite_children_is_ignored(tmp_path):
    geo = {
        "pages": [{
            "segments": [
                {"name": "500", "segmentCategory": "SECTION", "segments": []},
            ],
        }],
    }
    f = tmp_path / "seatmap_geo.json"
    f.write_text(json.dumps(geo))

    sections = gts.load_geo_multi(str(f), set(), {})

    assert sections == {}
