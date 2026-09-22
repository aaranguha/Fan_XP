"""
Tests for compare_snapshots.compare() — the core no-show detection logic.

A seat/listing that appears in BOTH the pre_game and halftime snapshots was
never claimed between the two scrapes, so it is a confirmed no-show. This is
the single most important function in the whole pipeline: every dashboard
number and phantom-revenue figure traces back to it.
"""

from compare_snapshots import compare


# ── Seat-level schema (current, keyed by section/row/seat) ─────────────────

def test_seat_present_in_both_snapshots_is_a_no_show():
    pre = [{"section": "101", "row": "5", "seat": "10", "price_usd": "150"}]
    halftime = [{"section": "101", "row": "5", "seat": "10", "price_usd": "150"}]

    result = compare(pre, halftime)

    assert len(result) == 1
    assert result[0]["seat"] == "10"


def test_seat_sold_between_snapshots_is_not_a_no_show():
    pre = [{"section": "101", "row": "5", "seat": "10", "price_usd": "150"}]
    halftime = []  # seat sold, no longer listed for resale

    assert compare(pre, halftime) == []


def test_new_halftime_listing_not_in_pregame_is_not_a_no_show():
    # A listing that only shows up at halftime (wasn't for sale pre-game)
    # must never be counted — no-shows are strictly pre_game ∩ halftime.
    pre = [{"section": "101", "row": "5", "seat": "10", "price_usd": "150"}]
    halftime = [
        {"section": "101", "row": "5", "seat": "10", "price_usd": "150"},
        {"section": "101", "row": "6", "seat": "1", "price_usd": "90"},
    ]

    result = compare(pre, halftime)

    assert len(result) == 1
    assert result[0]["seat"] == "10"


# ── Offer-level schema (old, keyed by offer_id) ─────────────────────────────

def test_offer_level_schema_still_detects_no_shows():
    pre = [{"offer_id": "abc123", "section": "Lower Bowl", "price_usd": "80"}]
    halftime = [{"offer_id": "abc123"}]

    result = compare(pre, halftime)

    assert len(result) == 1
    assert result[0]["offer_id"] == "abc123"


def test_offer_level_schema_sold_offer_excluded():
    pre = [{"offer_id": "abc123", "section": "Lower Bowl", "price_usd": "80"}]
    halftime = [{"offer_id": "different-offer"}]

    assert compare(pre, halftime) == []


# ── Schema mismatch and empty-input safety ──────────────────────────────────

def test_mixed_schema_returns_empty_instead_of_crashing():
    # A seat-level snapshot compared against an offer-level one used to be
    # able to KeyError on a missing "seat"/"offer_id" column. compare() must
    # detect the mismatch and bail out safely rather than raise.
    seat_level = [{"section": "101", "row": "5", "seat": "10"}]
    offer_level = [{"offer_id": "abc123"}]

    assert compare(seat_level, offer_level) == []
    assert compare(offer_level, seat_level) == []


def test_empty_snapshots_return_empty_list():
    assert compare([], []) == []
    assert compare([], [{"section": "101", "row": "5", "seat": "10"}]) == []
    assert compare([{"section": "101", "row": "5", "seat": "10"}], []) == []


# ── Output ordering ──────────────────────────────────────────────────────────

def test_no_shows_sorted_by_section_then_numeric_row_then_numeric_seat():
    pre = [
        {"section": "101", "row": "10", "seat": "2"},
        {"section": "101", "row": "2", "seat": "10"},
        {"section": "101", "row": "2", "seat": "2"},
    ]
    halftime = list(pre)  # nothing sold, all become no-shows

    result = compare(pre, halftime)

    # Numeric-aware ordering: row "2" sorts before row "10" (not lexicographic
    # "10" < "2"), and within row "2", seat "2" sorts before seat "10".
    ordered = [(r["row"], r["seat"]) for r in result]
    assert ordered == [("2", "2"), ("2", "10"), ("10", "2")]


def test_sort_does_not_crash_on_non_numeric_general_admission_rows():
    # General-admission listings can have non-numeric row/seat values (e.g.
    # "GA"); the zfill-based numeric sort must fall back gracefully instead
    # of raising on non-digit strings.
    pre = [
        {"section": "GA", "row": "GA", "seat": "GA"},
        {"section": "GA", "row": "1", "seat": "1"},
    ]
    halftime = list(pre)

    result = compare(pre, halftime)

    assert len(result) == 2
