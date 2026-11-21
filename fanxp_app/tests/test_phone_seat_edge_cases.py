"""
Edge-case coverage for fanxp_accounts.py's two pure string-parsing helpers,
normalize_phone() and expand_seats(), beyond the handful of cases already
covered in test_accounts.py.

Both feed data that reaches real people and real seat requests (a seller's
registered phone number; a buyer-entered seat range on sell.html), so a
parsing mistake here either silently drops a valid input (None where a real
phone should normalize) or lets a malformed one through un-rejected.
"""

import pytest

import fanxp_accounts


# ── normalize_phone() ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [
    # Bare 10-digit US number (no country code) is the common case.
    ("4155550100", "+14155550100"),
    # 11 digits starting with a leading "1" is already E.164-shaped minus
    # the plus sign.
    ("14155550100", "+14155550100"),
    # 11 digits NOT starting with "1" isn't a US number this function
    # recognizes, so it must be rejected rather than guessed at.
    ("24155550100", None),
    # Already "+"-prefixed numbers are kept as-is (digit count only
    # validated), supporting non-US numbers too.
    ("+1 415 555 0100", "+14155550100"),
    # "+"-prefixed but too short to be a real international number.
    ("+1234567", None),
    # "+"-prefixed at the exact 15-digit ITU-max boundary is accepted.
    ("+123456789012345", "+123456789012345"),
    # One digit past the boundary is rejected.
    ("+1234567890123456", None),
    # Garbage, empty, and missing input must not crash and must not be
    # treated as a valid phone number.
    ("", None),
    (None, None),
    ("abcdefg", None),
    # Formatting noise (parens, dashes, surrounding whitespace) is fine as
    # long as the underlying digit count is right.
    ("  (415) 555-0100  ", "+14155550100"),
    # 9 digits (one short of a real US number, no country code) is rejected
    # rather than silently padded.
    ("415-555-010", None),
])
def test_normalize_phone_edge_cases(raw, want):
    assert fanxp_accounts.normalize_phone(raw) == want


# ── expand_seats() ────────────────────────────────────────────────────────────

def test_expand_seats_single_number_is_a_one_item_list():
    assert fanxp_accounts.expand_seats("12") == ["12"]


def test_expand_seats_single_seat_range_collapses_to_one_item():
    assert fanxp_accounts.expand_seats("5-5") == ["5"]


@pytest.mark.parametrize("spec", ["", None, "   "])
def test_expand_seats_empty_or_blank_input_returns_empty_list(spec):
    assert fanxp_accounts.expand_seats(spec) == []


def test_expand_seats_strips_whitespace_around_comma_separated_parts():
    assert fanxp_accounts.expand_seats(" 5 , 7 ") == ["5", "7"]


def test_expand_seats_tolerates_whitespace_around_the_range_hyphen():
    assert fanxp_accounts.expand_seats("5 - 8") == ["5", "6", "7", "8"]


def test_expand_seats_skips_empty_parts_from_doubled_commas():
    assert fanxp_accounts.expand_seats("5,,7") == ["5", "7"]


def test_expand_seats_non_numeric_label_passes_through_unchanged():
    assert fanxp_accounts.expand_seats("AA") == ["AA"]


def test_expand_seats_range_of_exactly_40_seats_is_the_allowed_ceiling():
    # hi - lo == 39 (a 40-seat span) is the documented ceiling and must NOT
    # raise.
    seats = fanxp_accounts.expand_seats("1-40")
    assert len(seats) == 40
    assert seats[0] == "1" and seats[-1] == "40"


def test_expand_seats_range_of_41_seats_is_rejected_as_too_wide():
    # hi - lo == 40 (a 41-seat span) is one past the ceiling and must raise,
    # guarding against a typo locking out (or spamming) an unreasonable
    # number of seats at once.
    with pytest.raises(ValueError):
        fanxp_accounts.expand_seats("1-41")
