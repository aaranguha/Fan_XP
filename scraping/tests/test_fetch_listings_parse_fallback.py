"""
Tests for fetch_listings.py's facet-matching fallback: parse_seats() and
parse_facet().

CLAUDE.md §4.1 documents that `build_rows_from_embedded_offers()` is now the
*primary* data path, with this older facet-matching code kept only as a
fallback -- and explicitly warns against assuming it's broken if prices come
back empty again, since the section-field removal incident (Sept 2026) hit
the facet-matching join specifically. That join (parse_seats's
section-price lookup, joined against expanded/decoded place strings) had
zero test coverage despite being the thing that historically broke silently.
parse_facet() is the equivalent join for the older offer-level schema.
"""

import base64

import fetch_listings as fl

SCRAPED_AT = "2026-09-27T12:00:00Z"


def _b32(place_str):
    """Encode 'section:row:seat' the way TM's real place strings are encoded."""
    return base64.b32encode(place_str.encode("ascii")).decode("ascii").rstrip("=")


# ── parse_seats: section price join (offers -> offer_price_map) ────────────

def test_parse_seats_joins_price_via_offer_price_map():
    places_facets = [{
        "section": "116",
        "offers": ["off-1"],
        "inventoryTypes": ["resale"],
        "places": [_b32("116:5:23")],
    }]
    offer_price_map = {"off-1": 150.0}

    rows = fl.parse_seats([], places_facets, offer_price_map, SCRAPED_AT)

    assert len(rows) == 1
    row = rows[0]
    assert row["section"] == "116"
    assert row["row"] == "5"
    assert row["seat"] == "23"
    assert row["price_usd"] == 150.0
    assert row["selection_type"] == "resale"
    assert row["scraped_at"] == SCRAPED_AT


def test_parse_seats_non_resale_inventory_type_is_standard():
    places_facets = [{
        "section": "200",
        "offers": ["off-9"],
        "inventoryTypes": ["primary"],
        "places": [_b32("200:1:1")],
    }]
    offer_price_map = {"off-9": 40.0}

    rows = fl.parse_seats([], places_facets, offer_price_map, SCRAPED_AT)

    assert rows[0]["selection_type"] == "standard"


def test_parse_seats_missing_inventory_types_defaults_to_standard():
    places_facets = [{
        "section": "200",
        "offers": ["off-9"],
        "places": [_b32("200:1:1")],
    }]
    offer_price_map = {"off-9": 40.0}

    rows = fl.parse_seats([], places_facets, offer_price_map, SCRAPED_AT)

    assert rows[0]["selection_type"] == "standard"


# ── parse_seats: price fallback chain ───────────────────────────────────────

def test_parse_seats_falls_back_to_price_range_when_offer_price_missing():
    # No usable offer_price_map entry -- must fall back to the facet's own
    # totalPriceRange/priceRange before giving up.
    places_facets = [{
        "section": "116",
        "offers": ["off-missing"],
        "totalPriceRange": [{"min": 99.0}],
        "places": [_b32("116:5:23")],
    }]

    rows = fl.parse_seats([], places_facets, {}, SCRAPED_AT)

    assert rows[0]["price_usd"] == 99.0


def test_parse_seats_price_range_falls_back_to_low_key():
    places_facets = [{
        "section": "116",
        "offers": [],
        "priceRange": [{"low": 42.0}],
        "places": [_b32("116:5:23")],
    }]

    rows = fl.parse_seats([], places_facets, {}, SCRAPED_AT)

    assert rows[0]["price_usd"] == 42.0


def test_parse_seats_falls_back_to_section_level_price():
    # Last resort: a synthetic "__sec__{section}" key in offer_price_map.
    places_facets = [{
        "section": "305",
        "offers": [],
        "places": [_b32("305:1:1")],
    }]
    offer_price_map = {"__sec__305": 25.0}

    rows = fl.parse_seats([], places_facets, offer_price_map, SCRAPED_AT)

    assert rows[0]["price_usd"] == 25.0


def test_parse_seats_price_is_none_when_nothing_available():
    places_facets = [{
        "section": "999",
        "offers": [],
        "places": [_b32("999:1:1")],
    }]

    rows = fl.parse_seats([], places_facets, {}, SCRAPED_AT)

    assert rows[0]["price_usd"] is None
    assert rows[0]["selection_type"] == ""


def test_parse_seats_keeps_cheapest_price_across_duplicate_section_facets():
    # Same section appearing in both all_facets and places_facets (the
    # documented reason _apply_facet_prices runs over both lists) with
    # different prices -- the cheaper one must win.
    all_facets = [{
        "section": "116",
        "offers": ["expensive"],
    }]
    places_facets = [{
        "section": "116",
        "offers": ["cheap"],
        "places": [_b32("116:5:23")],
    }]
    offer_price_map = {"expensive": 500.0, "cheap": 50.0}

    rows = fl.parse_seats(all_facets, places_facets, offer_price_map, SCRAPED_AT)

    assert rows[0]["price_usd"] == 50.0


def test_parse_seats_skips_facets_with_no_section():
    all_facets = [{"offers": ["off-1"]}]  # no "section" key at all
    places_facets = [{
        "section": "116",
        "offers": ["off-1"],
        "places": [_b32("116:5:23")],
    }]
    offer_price_map = {"off-1": 10.0}

    # Should not raise despite the section-less facet in all_facets.
    rows = fl.parse_seats(all_facets, places_facets, offer_price_map, SCRAPED_AT)

    assert len(rows) == 1
    assert rows[0]["price_usd"] == 10.0


# ── parse_seats: place decoding / expansion ─────────────────────────────────

def test_parse_seats_falls_back_to_facet_section_label_when_place_has_none():
    # decode_place() returns (None, None, None) for text that doesn't decode
    # to 3 colon-separated parts -- parse_seats must fall back to the
    # facet's own "section" label rather than dropping the row, as long as
    # a row/seat was still recoverable from elsewhere. Here we exercise the
    # "sec or section_label" branch directly via a well-formed place whose
    # encoded section is empty.
    places_facets = [{
        "section": "LABEL-SEC",
        "offers": [],
        "places": [_b32(":5:23")],  # empty section component
    }]

    rows = fl.parse_seats([], places_facets, {}, SCRAPED_AT)

    assert len(rows) == 1
    assert rows[0]["section"] == "LABEL-SEC"
    assert rows[0]["row"] == "5"
    assert rows[0]["seat"] == "23"


def test_parse_seats_drops_places_missing_row_or_seat():
    # decode_place() returns (None, None, None) when a place string doesn't
    # decode into exactly 3 parts -- those must be silently dropped, not
    # turned into a bogus row with empty row/seat.
    places_facets = [{
        "section": "116",
        "offers": [],
        "places": [_b32("116:5"), _b32("116:5:23")],  # first is malformed
    }]

    rows = fl.parse_seats([], places_facets, {}, SCRAPED_AT)

    assert len(rows) == 1
    assert rows[0]["seat"] == "23"


def test_parse_seats_expands_every_place_in_a_facets_places_list():
    # Each facet's "places" list can hold several independently-compressed
    # place strings; every one must turn into its own seat row.
    places_facets = [{
        "section": "116",
        "offers": [],
        "places": [_b32("116:5:1"), _b32("116:5:2"), _b32("116:5:3")],
    }]

    rows = fl.parse_seats([], places_facets, {}, SCRAPED_AT)

    assert [r["seat"] for r in rows] == ["1", "2", "3"]
    assert all(r["row"] == "5" and r["section"] == "116" for r in rows)


def test_parse_seats_empty_places_facets_yields_no_rows():
    assert fl.parse_seats([], [], {}, SCRAPED_AT) == []


# ── parse_facet (old offer-level schema) ────────────────────────────────────

def test_parse_facet_builds_one_row_per_offer():
    facet = {
        "section": "Lower Bowl",
        "count": 4,
        "inventoryTypes": ["resale"],
        "offers": ["o1", "o2"],
    }
    offer_price_map = {"o1": 80.0, "o2": 95.0}

    rows = fl.parse_facet(facet, offer_price_map, SCRAPED_AT)

    assert len(rows) == 2
    assert {r["offer_id"] for r in rows} == {"o1", "o2"}
    assert all(r["section"] == "Lower Bowl" for r in rows)
    assert all(r["quantity"] == 4 for r in rows)
    assert all(r["selection_type"] == "resale" for r in rows)
    assert all(r["scraped_at"] == SCRAPED_AT for r in rows)
    by_id = {r["offer_id"]: r["price_usd"] for r in rows}
    assert by_id == {"o1": 80.0, "o2": 95.0}


def test_parse_facet_falls_back_to_section_level_price_per_offer():
    facet = {
        "section": "Upper Deck",
        "offers": ["missing-1", "missing-2"],
    }
    offer_price_map = {"__sec__Upper Deck": 30.0}

    rows = fl.parse_facet(facet, offer_price_map, SCRAPED_AT)

    assert all(r["price_usd"] == 30.0 for r in rows)


def test_parse_facet_price_is_none_with_no_fallback_available():
    facet = {"section": "Nowhere", "offers": ["x"]}

    rows = fl.parse_facet(facet, {}, SCRAPED_AT)

    assert rows[0]["price_usd"] is None


def test_parse_facet_quantity_defaults_to_zero_when_missing():
    facet = {"section": "A", "offers": ["x"]}

    rows = fl.parse_facet(facet, {"x": 10.0}, SCRAPED_AT)

    assert rows[0]["quantity"] == 0


def test_parse_facet_non_resale_inventory_type_is_standard():
    facet = {"section": "A", "inventoryTypes": ["primary"], "offers": ["x"]}

    rows = fl.parse_facet(facet, {"x": 10.0}, SCRAPED_AT)

    assert rows[0]["selection_type"] == "standard"


def test_parse_facet_strips_whitespace_from_section():
    facet = {"section": "  A  ", "offers": ["x"]}

    rows = fl.parse_facet(facet, {"x": 10.0}, SCRAPED_AT)

    assert rows[0]["section"] == "A"


def test_parse_facet_empty_offers_yields_no_rows():
    facet = {"section": "A", "offers": []}

    assert fl.parse_facet(facet, {}, SCRAPED_AT) == []
