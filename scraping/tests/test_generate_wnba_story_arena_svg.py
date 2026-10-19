"""
Tests for generate_wnba_story.py's arena-heatmap SVG helpers: _rate_color(),
_arc(), and _mid_pt(). These draw the per-section no-show-rate visualization
on the WNBA team story pages (docs/wnba_{slug}_story.html) and have never
had test coverage -- generate_team_story.py has its own, separately
implemented and already-tested _rate_color() (test_generate_team_story.py),
but generate_wnba_story.py's copy is a distinct function in a distinct
module and a regression here would not be caught by those tests.

- _rate_color(): the green->amber->red gradient driving each section's fill
  color, keyed by its no-show rate. Two-branch piecewise formula (0-0.5 and
  0.5-1.0), with input clamped to [0, 1] since a section's share of a game's
  no-shows should never fall outside that range but is derived from live
  data, not a hardcoded constant.
- _arc(): the SVG path for one section's wedge in the ring. The large-arc-
  flag must flip only when the wedge's angular span exceeds 180 degrees --
  get this wrong and a single section spanning most/all of a ring (e.g. one
  real section in a ring of one) draws as the *short* way around the circle
  instead of the long way, i.e. a sliver instead of the full ring.
- _mid_pt(): the point used to place a section's label, at the angular and
  radial midpoint of its wedge.
"""

import math

import pytest

import generate_wnba_story as gws


# ── _rate_color() ────────────────────────────────────────────────────────────

def test_rate_color_zero_is_green():
    assert gws._rate_color(0.0) == "#00e5a0"


def test_rate_color_midpoint_is_amber():
    assert gws._rate_color(0.5) == "#ffd700"


def test_rate_color_one_is_red():
    assert gws._rate_color(1.0) == "#ff4d6d"


def test_rate_color_below_zero_clamps_to_zero():
    assert gws._rate_color(-5.0) == gws._rate_color(0.0)


def test_rate_color_above_one_clamps_to_one():
    assert gws._rate_color(5.0) == gws._rate_color(1.0)


def test_rate_color_interpolates_within_the_lower_half():
    # Below the 0.5 midpoint, the color must sit strictly between the pure
    # green (0.0) and pure amber (0.5) endpoints, not jump straight to one.
    color = gws._rate_color(0.25)
    assert color not in (gws._rate_color(0.0), gws._rate_color(0.5))
    assert color == "#7fde50"


def test_rate_color_interpolates_within_the_upper_half():
    color = gws._rate_color(0.75)
    assert color not in (gws._rate_color(0.5), gws._rate_color(1.0))
    assert color == "#ff9236"


def test_rate_color_always_returns_a_six_digit_hex_string():
    for r in (-1.0, 0.0, 0.1, 0.5, 0.9, 1.0, 2.0):
        color = gws._rate_color(r)
        assert color.startswith("#")
        assert len(color) == 7
        int(color[1:], 16)  # raises ValueError if not valid hex


# ── _arc() ────────────────────────────────────────────────────────────────

def test_arc_path_has_the_expected_svg_command_sequence():
    path = gws._arc(0, 0, 10, 10, 20, 20, 0, 90, gap=0)
    assert path.startswith("M")
    assert path.endswith("Z")
    assert path.count("A") == 2  # inner arc + outer arc
    assert path.count("L") == 1  # the connecting line between them


def test_arc_small_span_uses_the_short_large_arc_flag():
    # A 90-degree wedge is well under 180 degrees: both arc commands must
    # use large-arc-flag 0 (take the short way around).
    path = gws._arc(0, 0, 10, 10, 20, 20, 0, 90, gap=0)
    assert "A10,10 0 0,1" in path
    assert "A20,20 0 0,0" in path


def test_arc_span_just_over_180_degrees_flips_to_the_large_arc_flag():
    # A span of exactly 180 degrees must NOT trip the large-arc flag (a
    # half-circle is still drawn the "short" way); crossing just past it
    # must flip both flags to 1, or a lone section spanning nearly a full
    # ring would render as a thin sliver instead of the whole ring.
    at_180 = gws._arc(0, 0, 10, 10, 20, 20, 90, -90, gap=0)
    assert "A10,10 0 0,1" in at_180
    assert "A20,20 0 0,0" in at_180

    just_over = gws._arc(0, 0, 10, 10, 20, 20, 90.1, -90, gap=0)
    assert "A10,10 0 1,1" in just_over
    assert "A20,20 0 1,0" in just_over


def test_arc_gap_shrinks_the_wedge_on_both_sides():
    # gap pulls the start angle later and the end angle earlier (sd - gap,
    # ed + gap), so a non-zero gap must produce a different path than gap=0
    # -- this is what creates the visible sliver of background between
    # adjacent section wedges in the ring.
    no_gap = gws._arc(0, 0, 10, 10, 20, 20, 0, 90, gap=0)
    with_gap = gws._arc(0, 0, 10, 10, 20, 20, 0, 90, gap=5)
    assert no_gap != with_gap


def test_arc_does_not_crash_on_a_full_circle_single_section():
    # ring() calls _arc with sd=180, ed=-180 when a ring has exactly one
    # section (sp = 360 / 1 = 360) -- confirm this real call shape still
    # produces a valid closed path instead of raising or degenerating.
    path = gws._arc(0, 0, 10, 10, 20, 20, 180, -180, gap=0.8)
    assert path.startswith("M")
    assert path.endswith("Z")


# ── _mid_pt() ────────────────────────────────────────────────────────────────

def test_mid_pt_sits_at_the_radial_midpoint_on_the_bisector_angle():
    # Wedge from 0 to 180 degrees, inner radius 0, outer radius 10: the
    # bisector is at 90 degrees and the midpoint radius is (0+10)/2 = 5, so
    # the label point should land at (cx, cy+5).
    x, y = gws._mid_pt(0, 0, 0, 0, 10, 10, 0, 180)
    assert x == pytest.approx(0, abs=1e-9)
    assert y == pytest.approx(5)


def test_mid_pt_is_offset_from_an_off_center_arena_center():
    x, y = gws._mid_pt(5, 5, 0, 0, 10, 10, 90, 90)
    assert x == pytest.approx(5)
    assert y == pytest.approx(10)


def test_mid_pt_moves_outward_as_inner_and_outer_radii_grow():
    near = gws._mid_pt(0, 0, 0, 0, 10, 10, 0, 180)
    far = gws._mid_pt(0, 0, 20, 20, 30, 30, 0, 180)
    assert far[1] > near[1]
