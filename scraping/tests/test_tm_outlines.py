"""
Tests for extract_stadium_geo.py's tm_outlines() / section_names() — the
section-shape architecture CLAUDE.md §4.3 documents as the *current* fix for
the seat map's section outlines (superseding the convex_hull/min_area_rect
fallback covered in test_extract_stadium_geo.py, which is now only used for
any section tm_outlines() doesn't cover — currently none).

tm_outlines() reads Ticketmaster's own geometry JSON (the `placeDetailNoKeys`
response) and returns every drawable section-outline shape, each carrying
the section name(s) it covers and its text label. This is the data that
build_stadium_seatmap.py draws directly over TM's background art, and that
the live seat map's click targets rely on — a regression here silently
breaks section clicking or mislabels a section, exactly the kind of bug that
degrades only in production, not in the generator's own smoke test.

Verified live at 7051/7051 sections across all 32 NFL venues (CLAUDE.md
§4.3), so these functions are trusted with real production data; what's
missing is a regression net for their actual branching logic.
"""

from extract_stadium_geo import section_names, tm_outlines


# ── section_names() ─────────────────────────────────────────────────────────

def test_section_names_of_a_plain_section_segment_is_its_own_name():
    seg = {"segmentCategory": "SECTION", "name": "116"}

    assert section_names(seg) == ["116"]


def test_section_names_of_a_club_outline_collects_every_covered_section():
    # A club/SRO outline can cover several real sections at once (e.g. "C1"
    # covering club sections 116-125) -- confirmed live, CLAUDE.md §4.3.
    club = {
        "segmentCategory": "CLUB",
        "name": "Lumen Club",
        "segments": [
            {"segmentCategory": "SECTION", "name": "116"},
            {"segmentCategory": "SECTION", "name": "117"},
            {"segmentCategory": "SECTION", "name": "118"},
        ],
    }

    assert section_names(club) == ["116", "117", "118"]


def test_section_names_skips_row_level_children():
    # Rows are never themselves a source of further SECTION names, and must
    # not be descended into (a row's own "segments" are seats, not sections).
    section_with_rows = {
        "segmentCategory": "SECTION",
        "name": "116",
        "segments": [{"segmentCategory": "ROW", "name": "A", "segments": []}],
    }

    # The segment itself is already a SECTION, so it short-circuits to its
    # own name without even looking at the row children.
    assert section_names(section_with_rows) == ["116"]


def test_section_names_recurses_through_nested_non_section_groupings():
    nested = {
        "segmentCategory": "VENUE",
        "segments": [
            {
                "segmentCategory": "DECK",
                "segments": [
                    {"segmentCategory": "SECTION", "name": "340X96"},
                ],
            },
        ],
    }

    assert section_names(nested) == ["340X96"]


def test_section_names_missing_segments_key_returns_empty():
    assert section_names({"segmentCategory": "DECK"}) == []


def test_section_names_ignores_row_children_mixed_with_section_children():
    mixed = {
        "segmentCategory": "SRO",
        "segments": [
            {"segmentCategory": "ROW", "name": "GA-ROW", "segments": [
                {"segmentCategory": "SECTION", "name": "SHOULD-NOT-APPEAR"},
            ]},
            {"segmentCategory": "SECTION", "name": "SRO1"},
        ],
    }

    assert section_names(mixed) == ["SRO1"]


# ── tm_outlines() ────────────────────────────────────────────────────────────

def _section(name, shapes=None, segments=None):
    seg = {"segmentCategory": "SECTION", "name": name}
    if shapes is not None:
        seg["shapes"] = shapes
    if segments is not None:
        seg["segments"] = segments
    return seg


def test_tm_outlines_returns_one_entry_per_shape_with_its_section_name():
    page = {"segments": [
        _section("116", shapes=[{"path": "M0 0 L1 1", "labels": []}]),
    ]}

    out = tm_outlines(page)

    assert len(out) == 1
    assert out[0]["d"] == "M0 0 L1 1"
    assert out[0]["secs"] == ["116"]
    assert out[0]["label"] is None


def test_tm_outlines_shape_without_a_path_is_skipped():
    page = {"segments": [
        _section("116", shapes=[{"labels": [{"text": "116"}]}]),  # no "path"
    ]}

    assert tm_outlines(page) == []


def test_tm_outlines_takes_only_the_first_label_when_several_exist():
    page = {"segments": [
        _section("116", shapes=[{
            "path": "M0 0",
            "labels": [
                {"text": "116", "x": 10, "y": 20, "size": 90, "angle": 5, "styleId": "nroot"},
                {"text": "ignored-second-label", "x": 99, "y": 99},
            ],
        }]),
    ]}

    label = tm_outlines(page)[0]["label"]
    assert label == {"text": "116", "x": 10, "y": 20, "size": 90, "angle": 5, "style": "nroot"}


def test_tm_outlines_label_field_defaults_fill_in_missing_values():
    # Only "text" present -- x/y/size/angle/style must fall back to their
    # documented defaults rather than being omitted or raising a KeyError.
    page = {"segments": [
        _section("116", shapes=[{"path": "M0 0", "labels": [{"text": "116"}]}]),
    ]}

    label = tm_outlines(page)[0]["label"]
    assert label == {"text": "116", "x": 0, "y": 0, "size": 100, "angle": 0, "style": "nroot"}


def test_tm_outlines_one_shape_covering_several_sections_lists_them_all():
    # A club/SRO outline piece that covers multiple real sections at once.
    club = {
        "segmentCategory": "CLUB",
        "shapes": [{"path": "M0 0 L2 2", "labels": [{"text": "Club"}]}],
        "segments": [
            {"segmentCategory": "SECTION", "name": "C1"},
            {"segmentCategory": "SECTION", "name": "C2"},
        ],
    }
    page = {"segments": [club]}

    out = tm_outlines(page)

    assert len(out) == 1
    assert out[0]["secs"] == ["C1", "C2"]


def test_tm_outlines_section_with_multiple_outline_pieces_yields_multiple_entries():
    # Some real sections have 2+ outline pieces with their own labels
    # (e.g. Lumen 116-125, Gillette 300s -- CLAUDE.md §4.3). Both pieces
    # must come through, each still tagged with the same section name.
    page = {"segments": [
        _section("120", shapes=[
            {"path": "M0 0 L1 0", "labels": [{"text": "120 (lower)"}]},
            {"path": "M0 1 L1 1", "labels": [{"text": "120 (upper)"}]},
        ]),
    ]}

    out = tm_outlines(page)

    assert len(out) == 2
    assert [o["secs"] for o in out] == [["120"], ["120"]]
    assert {o["label"]["text"] for o in out} == {"120 (lower)", "120 (upper)"}


def test_tm_outlines_recurses_into_nested_non_row_groupings():
    page = {"segments": [
        {
            "segmentCategory": "DECK",
            "segments": [
                _section("340X96", shapes=[{"path": "M5 5", "labels": []}]),
            ],
        },
    ]}

    out = tm_outlines(page)

    assert len(out) == 1
    assert out[0]["secs"] == ["340X96"]


def test_tm_outlines_does_not_descend_into_row_level_children():
    # A ROW segment's own shapes (if any existed) must never surface --
    # outlines are drawn at the section/grouping level, not per-row.
    page = {"segments": [
        {
            "segmentCategory": "SECTION",
            "name": "116",
            "shapes": [{"path": "M0 0", "labels": []}],
            "segments": [
                {
                    "segmentCategory": "ROW",
                    "name": "A",
                    "shapes": [{"path": "SHOULD-NOT-APPEAR", "labels": []}],
                    "segments": [],
                },
            ],
        },
    ]}

    out = tm_outlines(page)

    assert len(out) == 1
    assert out[0]["d"] == "M0 0"


def test_tm_outlines_empty_page_returns_empty_list():
    assert tm_outlines({}) == []
    assert tm_outlines({"segments": []}) == []


def test_tm_outlines_section_with_no_shapes_key_contributes_nothing():
    page = {"segments": [_section("116")]}

    assert tm_outlines(page) == []
