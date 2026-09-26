"""
Tests for fetch_listings.py's seat-level decoding/row-building logic.

Covers the primary embedded-offer row-builder (`build_rows_from_embedded_offers`)
that CLAUDE.md §4.1 documents as the fix for a real Sept 2026 incident: TM
removed the "section" field from the inventory/places facets responses, which
silently broke the old facet-matching join (confirmed live: 0 of 1228 scraped
rows had a price). `build_rows_from_embedded_offers` is now the primary data
path specifically because it needs no cross-referencing.

Also covers the TM place-string trie decoder (`expand_place_string` /
`decode_place` / `_expand_seat_range`), which had zero coverage despite being
recursive, bit-fiddly (base32 padding), and used by the still-present
facet-matching fallback (`parse_seats`).
"""

import base64

import fetch_listings as fl


def _b32(place_str):
    """Encode 'section:row:seat' the way TM's real place strings are encoded."""
    return base64.b32encode(place_str.encode("ascii")).decode("ascii").rstrip("=")


# ── decode_place ────────────────────────────────────────────────────────────

def test_decode_place_roundtrips_section_row_seat():
    assert fl.decode_place(_b32("116:5:23")) == ("116", "5", "23")


def test_decode_place_strips_whitespace_in_parts():
    assert fl.decode_place(_b32("116: 5 : 23 ")) == ("116", "5", "23")


def test_decode_place_ga_seat_labels():
    assert fl.decode_place(_b32("A:GA:1")) == ("A", "GA", "1")


def test_decode_place_invalid_base32_returns_none_triple():
    assert fl.decode_place("!!!not-base32!!!") == (None, None, None)


def test_decode_place_too_few_parts_returns_none_triple():
    # "116:5" (only 2 parts) decodes cleanly but has no seat.
    assert fl.decode_place(_b32("116:5")) == (None, None, None)


# ── expand_place_string ──────────────────────────────────────────────────────

def test_expand_place_string_no_brackets_is_single_value():
    assert fl.expand_place_string("ABC") == ["ABC"]


def test_expand_place_string_single_level_bracket_shares_prefix():
    # "AB[C,D]" means two place strings sharing the "AB" prefix: ABC, ABD.
    assert fl.expand_place_string("AB[C,D]") == ["ABC", "ABD"]


def test_expand_place_string_nested_brackets():
    # "A[B[C,D],E]" -> ABC, ABD (nested), AE (sibling alternative).
    assert fl.expand_place_string("A[B[C,D],E]") == ["ABC", "ABD", "AE"]


def test_expand_place_string_empty_input():
    assert fl.expand_place_string("") == []


def test_expand_place_string_feeds_decode_place_end_to_end():
    # The real pipeline (parse_seats()): expand a compressed places string,
    # then decode each individual place back to (section, row, seat).
    single = _b32("204:12:7")
    places = fl.expand_place_string(single)
    assert [fl.decode_place(p) for p in places] == [("204", "12", "7")]


# ── _expand_seat_range ───────────────────────────────────────────────────────

def test_expand_seat_range_normal_ascending():
    assert fl._expand_seat_range("5", "8") == ["5", "6", "7", "8"]


def test_expand_seat_range_handles_reversed_bounds():
    assert fl._expand_seat_range("8", "5") == ["5", "6", "7", "8"]


def test_expand_seat_range_single_seat():
    assert fl._expand_seat_range("12", "12") == ["12"]


def test_expand_seat_range_non_numeric_falls_back_to_seat_from():
    assert fl._expand_seat_range("GA", "GA") == ["GA"]


def test_expand_seat_range_non_numeric_seat_to_falls_back_to_seat_from():
    assert fl._expand_seat_range("A1", "A5") == ["A1"]


def test_expand_seat_range_none_inputs_yield_empty():
    assert fl._expand_seat_range(None, None) == []


def test_expand_seat_range_empty_string_yields_empty():
    assert fl._expand_seat_range("", "") == []


# ── build_rows_from_embedded_offers ─────────────────────────────────────────

SCRAPED_AT = "2026-09-27T12:00:00Z"


def test_build_rows_expands_seat_range_into_one_row_per_seat():
    offers = [{
        "inventoryType": "resale",
        "section": "116",
        "row": "A",
        "seatFrom": "5",
        "seatTo": "7",
        "totalPrice": 120.5,
    }]
    rows = fl.build_rows_from_embedded_offers(offers, SCRAPED_AT)
    assert [r["seat"] for r in rows] == ["5", "6", "7"]
    assert all(r["section"] == "116" and r["row"] == "A" for r in rows)
    assert all(r["price_usd"] == 120.5 for r in rows)
    assert all(r["selection_type"] == "resale" for r in rows)
    assert all(r["scraped_at"] == SCRAPED_AT for r in rows)


def test_build_rows_excludes_non_resale_offers():
    offers = [
        {"inventoryType": "primary", "section": "117", "row": "B",
         "seatFrom": "1", "seatTo": "1", "totalPrice": 50},
        {"inventoryType": "standard", "section": "118", "row": "C",
         "seatFrom": "1", "seatTo": "1", "totalPrice": 60},
    ]
    assert fl.build_rows_from_embedded_offers(offers, SCRAPED_AT) == []


def test_build_rows_falls_back_to_list_price_when_total_price_missing():
    offers = [{
        "inventoryType": "resale",
        "section": "118",
        "row": "C",
        "seatFrom": "3",
        "seatTo": "3",
        "totalPrice": None,
        "listPrice": 75,
    }]
    rows = fl.build_rows_from_embedded_offers(offers, SCRAPED_AT)
    assert len(rows) == 1
    assert rows[0]["price_usd"] == 75


def test_build_rows_price_is_none_when_both_prices_missing():
    offers = [{
        "inventoryType": "resale",
        "section": "119",
        "row": "D",
        "seatFrom": "1",
        "seatTo": "1",
    }]
    rows = fl.build_rows_from_embedded_offers(offers, SCRAPED_AT)
    assert rows[0]["price_usd"] is None


def test_build_rows_strips_whitespace_from_section_and_row():
    offers = [{
        "inventoryType": "resale",
        "section": " 116 ",
        "row": " A ",
        "seatFrom": "1",
        "seatTo": "1",
        "totalPrice": 10,
    }]
    rows = fl.build_rows_from_embedded_offers(offers, SCRAPED_AT)
    assert rows[0]["section"] == "116"
    assert rows[0]["row"] == "A"


def test_build_rows_empty_input_yields_empty_output():
    assert fl.build_rows_from_embedded_offers([], SCRAPED_AT) == []


def test_build_rows_multiple_offers_across_sections():
    offers = [
        {"inventoryType": "resale", "section": "100", "row": "1",
         "seatFrom": "1", "seatTo": "2", "totalPrice": 30},
        {"inventoryType": "resale", "section": "200", "row": "5",
         "seatFrom": "10", "seatTo": "10", "totalPrice": 45},
    ]
    rows = fl.build_rows_from_embedded_offers(offers, SCRAPED_AT)
    assert len(rows) == 3
    sections = {r["section"] for r in rows}
    assert sections == {"100", "200"}
