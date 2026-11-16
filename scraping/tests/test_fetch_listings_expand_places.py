"""
Tests for fetch_listings.py's `_expand_places()` / `expand_place_string()` —
the recursive trie-string expander behind the facet-matching fallback path
(`parse_seats`, still present per CLAUDE.md §4.1 as the backup to
`build_rows_from_embedded_offers`).

test_fetch_listings_seat_decode.py already covers the basic shapes (no
brackets, a single two-branch bracket, one level of nesting, empty input).
`_expand_places()` itself had zero direct coverage (its only caller is
`expand_place_string()`), and none of the existing cases exercise:

  - a bracket with MORE than two comma-separated branches (the realistic
    shape for a real TM row: "section:row:[seat1,seat2,seat3,...]" — a row
    with several resale seats is far more common than exactly two)
  - two levels of nesting where EACH level itself has multiple siblings
    (e.g. multiple rows, each with multiple seats, under one section)
  - malformed/truncated input (a bracket opened but never closed) — a
    truncated TM response is exactly the kind of "API changed/glitched
    under us" failure mode this project has hit before (CLAUDE.md §4.1's
    missing-"section"-field incident), so the parser silently degrading
    instead of raising is worth locking in as a contract, not an accident
  - a stray double comma (an empty branch slot) inside a bracket

These are regression tests for current, correct behavior (traced against
the live implementation before being written) — none of them reveal a bug.
"""

import fetch_listings as fl


# ── Multi-way branching within a single bracket ─────────────────────────────

def test_three_way_branch_in_one_bracket_expands_all_three():
    # The realistic shape: one row with three resale seats, e.g.
    # "116:5:[23,24,25]" compresses to this via a shared "116:5:" prefix.
    assert fl.expand_place_string("A[B,C,D]") == ["AB", "AC", "AD"]


def test_five_way_branch_in_one_bracket_expands_all_five():
    assert fl.expand_place_string("A[1,2,3,4,5]") == ["A1", "A2", "A3", "A4", "A5"]


def test_three_way_branch_feeds_decode_place_as_three_separate_seats():
    # End-to-end: one section/row prefix with three seats, each decoding
    # back to its own (section, row, seat) triple.
    import base64

    def b32(s):
        return base64.b32encode(s.encode("ascii")).decode("ascii").rstrip("=")

    # Build a single compressed string whose branches are themselves valid
    # base32 fragments completing a shared prefix, the way TM's real place
    # strings are structured.
    full = [b32("116:5:23"), b32("116:5:24"), b32("116:5:25")]
    # Find the longest common prefix across the three encodings and express
    # the rest as a bracketed trie, mirroring TM's own compression.
    common = full[0]
    for f in full[1:]:
        i = 0
        while i < len(common) and i < len(f) and common[i] == f[i]:
            i += 1
        common = common[:i]
    compressed = common + "[" + ",".join(f[len(common):] for f in full) + "]"

    places = fl.expand_place_string(compressed)
    assert len(places) == 3
    decoded = {fl.decode_place(p) for p in places}
    assert decoded == {("116", "5", "23"), ("116", "5", "24"), ("116", "5", "25")}


# ── Multiple siblings at more than one nesting level ─────────────────────────

def test_two_levels_of_nesting_each_with_multiple_siblings():
    # Section "A" has two rows ("B", "E"), each with two seats.
    assert fl.expand_place_string("A[B[1,2],E[3,4]]") == ["AB1", "AB2", "AE3", "AE4"]


def test_three_levels_of_nesting_with_siblings_at_every_level():
    assert fl.expand_place_string("A[B[C[1,2],D[3,4]],E[5,6]]") == [
        "ABC1", "ABC2", "ABD3", "ABD4", "AE5", "AE6",
    ]


# ── Malformed / truncated input is degraded, never raised ───────────────────

def test_bracket_opened_but_never_closed_does_not_raise():
    # A truncated API response (connection cut mid-body, or TM changing its
    # response shape again) must not crash the scrape — it should expand
    # whatever full branches it did see and stop there.
    assert fl.expand_place_string("A[B,C") == ["AB", "AC"]


def test_deeply_nested_bracket_missing_only_the_outermost_close():
    assert fl.expand_place_string("A[B[1,2],E[3,4]") == ["AB1", "AB2", "AE3", "AE4"]


def test_double_comma_inside_bracket_is_skipped_without_an_empty_entry():
    # A stray empty branch slot (two commas back to back) must not produce
    # a bogus empty-string place in the output.
    result = fl.expand_place_string("A[B,,C]")
    assert result == ["AB", "AC"]
    assert "" not in result


# ── Prefix isolation between sibling branches ────────────────────────────────

def test_sibling_branches_do_not_leak_characters_into_each_other():
    # Each branch must expand from the shared prefix independently -- a bug
    # that accumulated characters across siblings (e.g. reusing a mutated
    # prefix instead of the original) would make later branches too long.
    result = fl.expand_place_string("SEC[ROW1,ROW2,ROW3]")
    assert result == ["SECROW1", "SECROW2", "SECROW3"]
    assert all(len(r) == len("SECROW1") for r in result)
