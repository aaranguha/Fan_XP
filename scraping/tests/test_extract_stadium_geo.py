"""
Tests for extract_stadium_geo.py — the section-shape architecture CLAUDE.md
§4.3 documents as the permanent fix for a real, confirmed bug:

    Ticketmaster's own background SVG art uses several different,
    inconsistent id schemes across sections in the same venue ... and for
    roughly a third of real sections, no shape existed in their art at all.
    The fix, now the permanent architecture: extract_stadium_geo.py computes
    each section's own shape directly from its real seat coordinates (a
    minimum-area bounding rectangle around a convex hull of the seats) ...
    Verified via a real headless browser at 100% section coverage for two
    different venues (Lumen Field, Lambeau Field) after this change.

These are the pure geometry/parsing functions behind that architecture:
convex_hull(), min_area_rect(), tier_of(), normalize(), and find_sections().
A regression in any of them silently degrades section-shape coverage again —
exactly the failure mode this file exists to prevent.
"""

import math

from extract_stadium_geo import (
    convex_hull,
    find_sections,
    min_area_rect,
    normalize,
    tier_of,
)


# ── normalize() ──────────────────────────────────────────────────────────────

def test_normalize_strips_whitespace_and_uppercases():
    assert normalize("  116  ") == "116"
    assert normalize("row a") == "ROW A"


def test_normalize_handles_none_and_empty():
    assert normalize(None) == ""
    assert normalize("") == ""


# ── tier_of() ────────────────────────────────────────────────────────────────

def test_tier_of_club_sections_start_with_c():
    assert tier_of("C101") == "club"
    assert tier_of("C205") == "club"


def test_tier_of_suite_sections_start_with_p_or_contain_vip():
    assert tier_of("P5") == "suite"
    assert tier_of("VIP1") == "suite"
    assert tier_of("116 VIP") == "suite"


def test_tier_of_field_sections():
    assert tier_of("FLD12") == "field"
    assert tier_of("SR1") == "field"


def test_tier_of_check_order_prefix_wins_over_substring_checks():
    # A name starting with "C" is club even if it would otherwise also
    # satisfy a later branch — the starts-with checks run first.
    assert tier_of("C-VIP") == "club"


def test_tier_of_bowl_tiers_by_digit_range():
    assert tier_of("100") == "lower"
    assert tier_of("116") == "lower"
    assert tier_of("199") == "lower"
    assert tier_of("200") == "mezz"
    assert tier_of("250") == "mezz"
    assert tier_of("299") == "mezz"
    assert tier_of("300") == "upper"
    assert tier_of("450") == "upper"
    assert tier_of("499") == "upper"


def test_tier_of_boundaries_outside_named_ranges_fall_to_other():
    assert tier_of("99") == "other"
    assert tier_of("500") == "other"
    assert tier_of("1200") == "other"


def test_tier_of_extracts_digits_from_mixed_alnum_names():
    # Section names aren't always bare numbers — tier_of must still find
    # the digits inside a mixed alnum name.
    assert tier_of("116A") == "lower"
    assert tier_of("S116") == "lower"


def test_tier_of_concatenates_all_digits_rather_than_taking_a_prefix():
    # tier_of joins every digit character in the name (not just a leading
    # run), so a name with digits on both sides of a letter reads as one
    # concatenated number — pinning this down since it's easy to assume
    # (incorrectly) that only a leading numeric prefix is used.
    assert tier_of("340X96") == "other"  # "34096" is out of every named range


def test_tier_of_no_digits_at_all_is_other():
    # Must not start with "C"/"P" or contain "VIP"/"FLD"/start with "SR",
    # or an earlier branch would fire before the digit check.
    assert tier_of("ENDZONE") == "other"
    assert tier_of("") == "other"


# ── convex_hull() ────────────────────────────────────────────────────────────

def test_convex_hull_of_a_square_returns_its_four_corners():
    points = [(0, 0), (10, 0), (10, 10), (0, 10)]
    hull = convex_hull(points)

    assert set(hull) == set(points)
    assert len(hull) == 4


def test_convex_hull_drops_interior_points():
    points = [(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)]
    hull = convex_hull(points)

    assert (5, 5) not in hull
    assert len(hull) == 4


def test_convex_hull_deduplicates_repeated_points():
    points = [(0, 0), (0, 0), (10, 0), (10, 10), (0, 10)]
    hull = convex_hull(points)

    assert len(hull) == 4


def test_convex_hull_two_or_fewer_points_returns_them_unchanged():
    assert convex_hull([(1, 1)]) == [(1, 1)]
    assert set(convex_hull([(1, 1), (2, 2)])) == {(1, 1), (2, 2)}


def test_convex_hull_empty_input():
    assert convex_hull([]) == []


def test_convex_hull_collinear_points_degenerate_to_two_endpoints():
    # A single-row section (all seats on one line) has no interior — this
    # is exactly the case main() special-cases into a thin rectangle rather
    # than dropping the section (see the len(hull) < 3 branch in main()).
    points = [(0, 0), (1, 0), (2, 0), (3, 0)]
    hull = convex_hull(points)

    assert len(hull) == 2
    assert set(hull) == {(0, 0), (3, 0)}


# ── min_area_rect() ──────────────────────────────────────────────────────────

def test_min_area_rect_of_fewer_than_three_points_returns_input_unchanged():
    assert min_area_rect([(0, 0), (1, 1)]) == [(0, 0), (1, 1)]
    assert min_area_rect([]) == []


def _polygon_area(pts):
    # Shoelace formula.
    n = len(pts)
    total = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def test_min_area_rect_of_an_axis_aligned_rectangle_hull_is_that_rectangle():
    hull = [(0, 0), (10, 0), (10, 4), (0, 4)]
    rect = min_area_rect(hull)

    assert rect is not None
    assert len(rect) == 4
    assert math.isclose(_polygon_area(rect), 40.0, rel_tol=1e-6)


def test_min_area_rect_of_a_rotated_rectangle_matches_its_true_area():
    # A 10x4 rectangle rotated 30 degrees — its convex hull is itself (it's
    # already convex), and the tightest bounding rectangle around it must
    # still be that same rectangle (up to floating point), not the larger
    # axis-aligned bounding box.
    theta = math.radians(30)
    w, h = 10.0, 4.0
    corners = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    rotated = [
        (x * math.cos(theta) - y * math.sin(theta), x * math.sin(theta) + y * math.cos(theta))
        for x, y in corners
    ]

    rect = min_area_rect(rotated)

    assert math.isclose(_polygon_area(rect), w * h, rel_tol=1e-6)
    # The naive axis-aligned bounding box would be strictly larger, since
    # the rectangle is tilted — pin that the function found the *tighter*
    # rotated box, not just min/max of x and y.
    xs = [p[0] for p in rotated]
    ys = [p[1] for p in rotated]
    aabb_area = (max(xs) - min(xs)) * (max(ys) - min(ys))
    assert _polygon_area(rect) < aabb_area


def test_min_area_rect_triangle_hull_still_returns_four_corners():
    hull = [(0, 0), (4, 0), (0, 3)]
    rect = min_area_rect(hull)

    assert len(rect) == 4
    # The fitted rectangle must fully contain the original triangle's area.
    assert _polygon_area(rect) >= _polygon_area(hull) - 1e-6


# ── find_sections() ──────────────────────────────────────────────────────────

def test_find_sections_collects_only_section_category_segments():
    tree = {
        "segmentCategory": "VENUE",
        "segments": [
            {"segmentCategory": "LEVEL", "segments": [
                {"segmentCategory": "SECTION", "name": "116", "segments": []},
                {"segmentCategory": "SECTION", "name": "117", "segments": []},
            ]},
            {"segmentCategory": "SECTION", "name": "C101", "segments": []},
        ],
    }
    out = []
    find_sections(tree, out)

    names = sorted(s["name"] for s in out)
    assert names == ["116", "117", "C101"]


def test_find_sections_empty_tree_finds_nothing():
    out = []
    find_sections({"segmentCategory": "VENUE", "segments": []}, out)

    assert out == []


def test_find_sections_handles_missing_segments_key():
    out = []
    find_sections({"segmentCategory": "SECTION", "name": "340X96"}, out)

    assert out == [{"segmentCategory": "SECTION", "name": "340X96"}]
