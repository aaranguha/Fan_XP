"""
Tests for generate_wnba_story.py's arena-shell assembly helpers: _court() and
_arena_svg(). These sit one layer above the already-tested per-wedge drawing
primitives (_rate_color()/_arc()/_mid_pt(), covered in
test_generate_wnba_story_arena_svg.py) and had no coverage of their own:
_court() draws the static basketball-court markup at the center of the page,
and _arena_svg() is the top-level assembly that buckets every section into a
lower/upper ring, sorts each ring, and stitches the wedges + court together
into the full diagram embedded in docs/wnba_{slug}_story.html.

Also covers a real gap found while writing these tests (see the
"known gap" section at the bottom): _arena_svg()'s section bucketing keys
off a numeric prefix and only accepts section numbers in [100, 200) or
>= 200, silently dropping any section below 100. Real scraped data has
exactly these sections -- e.g. the Liberty's 2026-07-22 Chicago Sky game's
no_shows.csv has floor/courtside sections "1", "3", "6", "7", "8", "9",
"10", "12", et. -- so their no-show counts and phantom revenue, while
correctly aggregated elsewhere (build_games_data()'s sec_totals/top_sections),
never appear anywhere in this diagram.
"""

import pytest

import generate_wnba_story as gws


# ── _court() ─────────────────────────────────────────────────────────────────

def test_court_draws_the_main_rect_sized_and_centered_on_cx_cy():
    svg = gws._court(450, 310)
    assert '<rect x="345" y="254" width="210" height="112" fill="#c8922a" rx="3"/>' in svg


def test_court_recenters_when_cx_cy_change():
    svg = gws._court(0, 0)
    assert '<rect x="-105" y="-56" width="210" height="112" fill="#c8922a" rx="3"/>' in svg


def test_court_center_line_and_circle_sit_exactly_on_cx():
    cx, cy = 450, 310
    svg = gws._court(cx, cy)
    assert f'<line x1="{cx}" y1="254" x2="{cx}" y2="366"' in svg
    assert f'<circle cx="{cx}" cy="{cy}" r="13"' in svg


def test_court_draws_two_basket_assemblies_each_with_a_rim_and_backboard_dot():
    svg = gws._court(450, 310)
    # one basket near the left edge, one near the right: cx-105+11 and cx+105-11
    assert '<circle cx="356" cy="310" r="8"' in svg
    assert '<circle cx="544" cy="310" r="8"' in svg
    assert svg.count('fill="#e87722"') == 2


def test_court_shape_counts_are_fixed_and_deterministic():
    # 1 main rect + 2 key (lane) rects + 1 outer-border rect = 4 rects;
    # center circle + 4 basket circles = 5 circles; 4 free-throw-circle arcs
    # + 2 three-point-arc paths = 6 paths. A change to any of these without
    # an accompanying design change usually means a shape was dropped or
    # duplicated.
    svg = gws._court(450, 310)
    assert svg.count("<rect") == 4
    assert svg.count("<circle") == 5
    assert svg.count("<path") == 6


def test_court_is_a_single_newline_joined_string_not_a_list():
    svg = gws._court(450, 310)
    assert isinstance(svg, str)
    assert "\n" in svg


# ── _arena_svg() ─────────────────────────────────────────────────────────────

def test_arena_svg_with_no_sections_still_draws_background_and_court():
    svg = gws._arena_svg({})
    assert '<rect width="900" height="620" fill="#f4f3ef"/>' in svg
    assert svg.count("<ellipse") == 4  # outer bg + upper-inner + lower-outer + lower-inner
    assert '<circle cx="450" cy="310" r="13"' in svg  # the court is always appended


def test_arena_svg_places_a_lower_section_and_an_upper_section():
    sec_totals = {
        "105": {"ns": 10, "share": 0.5, "value": 1000},
        "210": {"ns": 2, "share": 0.1, "value": 200},
    }
    svg = gws._arena_svg(sec_totals)
    assert 'data-s="105"' in svg
    assert 'data-s="210"' in svg


def test_arena_svg_section_with_no_no_shows_renders_as_na_and_gray():
    sec_totals = {"150": {"ns": 0, "share": 0.0, "value": 0}}
    svg = gws._arena_svg(sec_totals)
    assert 'data-s="150" data-ns="0" data-r="N/A" data-v="N/A"' in svg
    assert 'fill="#d8d6d0"' in svg


def test_arena_svg_section_missing_from_sec_totals_dict_also_renders_as_na():
    # ring() looks the section back up via sec_totals.get(sec, {}); a section
    # name that made it into the lower/upper bucket but has no matching
    # entry must fall back to the empty-dict defaults, not raise.
    svg = gws._arena_svg({"120": {}})
    assert 'data-s="120" data-ns="0" data-r="N/A" data-v="N/A"' in svg


def test_arena_svg_section_with_no_shows_uses_rate_color_and_formatted_percent():
    sec_totals = {"101": {"ns": 4, "share": 0.5, "value": 1234}}
    svg = gws._arena_svg(sec_totals)
    assert f'fill="{gws._rate_color(0.5)}"' in svg
    assert 'data-r="50.0%"' in svg
    assert 'data-v="$1,234"' in svg


def test_arena_svg_only_labels_lower_bowl_sections_not_upper():
    # ring(lower, ..., label_inner=True) vs ring(upper, ..., label_inner=False):
    # only the lower-bowl ring draws a <text> label for its section number.
    sec_totals = {
        "101": {"ns": 1, "share": 0.2, "value": 50},
        "205": {"ns": 1, "share": 0.2, "value": 50},
    }
    svg = gws._arena_svg(sec_totals)
    assert ">101<" in svg
    assert ">205<" not in svg


def test_arena_svg_sorts_each_ring_numerically_not_by_insertion_order():
    # Sections are inserted out of numeric order; sec_num()-keyed sorted()
    # must still draw them in ascending numeric order (101, then 125, then
    # 150), which this asserts via the order the data-s markers appear in
    # the generated markup.
    sec_totals = {
        "150": {"ns": 1, "share": 0.1, "value": 10},
        "101": {"ns": 1, "share": 0.1, "value": 10},
        "125": {"ns": 1, "share": 0.1, "value": 10},
    }
    svg = gws._arena_svg(sec_totals)
    assert svg.index('data-s="101"') < svg.index('data-s="125"') < svg.index('data-s="150"')


def test_arena_svg_bucket_boundary_100_is_lower_199_is_lower_200_is_upper():
    sec_totals = {
        "100": {"ns": 1, "share": 0.1, "value": 10},
        "199": {"ns": 1, "share": 0.1, "value": 10},
        "200": {"ns": 1, "share": 0.1, "value": 10},
    }
    svg = gws._arena_svg(sec_totals)
    # the boundary sections must at least be drawn (bucket choice doesn't
    # change the data-s attribute itself, but label_inner does -- 100/199
    # are lower-bowl and get a label, 200 is upper and does not).
    assert ">100<" in svg
    assert ">199<" in svg
    assert ">200<" not in svg


def test_arena_svg_handles_a_single_section_per_ring_without_crashing():
    # ring() computes sp = 360 / n; n == 1 is the degenerate full-circle
    # case already covered at the _arc() level (test_generate_wnba_story
    # _arena_svg.py::test_arc_does_not_crash_on_a_full_circle_single_section)
    # -- this confirms _arena_svg() actually reaches that call shape.
    svg = gws._arena_svg({"101": {"ns": 1, "share": 1.0, "value": 100}})
    assert 'data-s="101"' in svg


# ── known gap: sections numbered below 100 are silently dropped ────────────

def test_arena_svg_KNOWN_GAP_sections_below_100_are_dropped_entirely():
    """
    _arena_svg()'s bucketing is `100 <= sec_num(s) < 200` for the lower
    bowl and `sec_num(s) >= 200` for the upper bowl -- there is no bucket
    for sec_num(s) < 100. Real WNBA venues sell floor/courtside sections
    numbered below 100 (confirmed in this repo's own scraped data:
    scraping/data/wnba/liberty/2026-07-22_chicago_sky_at_liberty/
    no_shows.csv has sections "1", "3", "6", "7", "8", "9", "10", "12",
    "16", "17", "19", "22", "23", "24", "25", "29", "31", plus "VIP23"/
    "VIP24"/"FLR9"/"FLR25"/"FLR31"/"25WC", all of which parse to a
    sec_num() under 100 since digits are extracted regardless of any
    letter prefix). Their no-show counts and phantom revenue ARE
    correctly aggregated elsewhere (build_games_data()'s sec_totals and
    top_sections), but this diagram never draws them -- not in either
    ring, not as a label, not even as a hidden/zero-size wedge. A fan or
    team viewing the arena visual has no way to tell these sections'
    data exists at all.

    This test pins the CURRENT (buggy) behavior as a regression guard,
    not the intended one -- see the separate GitHub issue filed alongside
    this PR for what the correct behavior should be (most likely: a third,
    innermost "floor" ring, or at minimum surfacing these sections' totals
    outside the diagram so the data isn't silently lost from the page).
    """
    sec_totals = {
        "7": {"ns": 5, "share": 0.4, "value": 500},
        "105": {"ns": 10, "share": 0.5, "value": 1000},
    }
    svg = gws._arena_svg(sec_totals)
    assert 'data-s="105"' in svg
    assert 'data-s="7"' not in svg
    assert ">7<" not in svg
