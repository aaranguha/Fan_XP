"""
Tests for fanxp_accounts.find_seat_owner() and get_or_create_account_seller()
— the lookup the real claim flow uses to decide whether a claimed seat
belongs to a seller with a real account (routing the request to them,
instead of the demo STH_PHONE seller) and, if so, which nfl_sellers row to
point the request at.

Per CLAUDE.md §6: "A claim on a registered seat routes to that seller (their
own nfl_sellers row, user_id set) instead of the demo STH_PHONE seller."
These two functions are the mechanism behind that sentence and were not
directly unit tested before (only exercised indirectly through the full
claim-flow route tests). Runs against the same in-memory fake Supabase
client used by test_accounts.py; no network or credentials.
"""

from types import SimpleNamespace

import pytest

import fanxp_accounts

from test_accounts import FakeDB, make_user


@pytest.fixture
def db():
    users = {"u-seller": make_user("u-seller", ["seller"], phone="+15555550100", name="Sam Seller")}
    fake = FakeDB(users)
    return fake


# ── find_seat_owner() ─────────────────────────────────────────────────────

def test_find_seat_owner_returns_the_registered_seller_seat(db):
    db.tables["seller_seats"] = [
        {"id": 1, "user_id": "u-seller", "team_slug": "49ers",
         "section": "119", "row_label": "12", "seat_num": "5"},
    ]

    owner = fanxp_accounts.find_seat_owner(db, "49ers", "119", "12", "5")

    assert owner is not None and owner["id"] == 1 and owner["user_id"] == "u-seller"


def test_find_seat_owner_normalizes_case_and_whitespace(db):
    db.tables["seller_seats"] = [
        {"id": 1, "user_id": "u-seller", "team_slug": "49ers",
         "section": "119", "row_label": "12", "seat_num": "5"},
    ]

    # Claim flow passes whatever the seat map/API gave it -- lowercase,
    # stray whitespace -- which must still match the stored (normalized) row.
    owner = fanxp_accounts.find_seat_owner(db, "49ers", " 119 ", "12", "5")

    assert owner is not None and owner["id"] == 1


def test_find_seat_owner_returns_none_when_seat_not_registered(db):
    db.tables["seller_seats"] = [
        {"id": 1, "user_id": "u-seller", "team_slug": "49ers",
         "section": "119", "row_label": "12", "seat_num": "5"},
    ]

    assert fanxp_accounts.find_seat_owner(db, "49ers", "119", "12", "6") is None


def test_find_seat_owner_does_not_cross_teams(db):
    # Same section/row/seat registered for a different team must not match.
    db.tables["seller_seats"] = [
        {"id": 1, "user_id": "u-seller", "team_slug": "raiders",
         "section": "119", "row_label": "12", "seat_num": "5"},
    ]

    assert fanxp_accounts.find_seat_owner(db, "49ers", "119", "12", "5") is None


# ── get_or_create_account_seller() ───────────────────────────────────────

def test_creates_a_new_nfl_sellers_row_when_none_exists(db):
    row = fanxp_accounts.get_or_create_account_seller(db, "u-seller", "49ers")

    assert row["user_id"] == "u-seller"
    assert row["team_slug"] == "49ers"
    assert row["section"] == "(account)"
    assert row["name"] == "Sam Seller"
    assert row["phone"] == "+15555550100"
    assert len(db.tables["nfl_sellers"]) == 1


def test_reuses_the_existing_row_for_the_same_user_and_team(db):
    first = fanxp_accounts.get_or_create_account_seller(db, "u-seller", "49ers")
    second = fanxp_accounts.get_or_create_account_seller(db, "u-seller", "49ers")

    assert second["id"] == first["id"]
    assert len(db.tables["nfl_sellers"]) == 1


def test_separate_rows_for_the_same_seller_across_different_teams(db):
    niners_row = fanxp_accounts.get_or_create_account_seller(db, "u-seller", "49ers")
    raiders_row = fanxp_accounts.get_or_create_account_seller(db, "u-seller", "raiders")

    assert niners_row["id"] != raiders_row["id"]
    assert len(db.tables["nfl_sellers"]) == 2


def test_stale_name_or_phone_on_the_existing_row_is_refreshed(db):
    fanxp_accounts.get_or_create_account_seller(db, "u-seller", "49ers")

    # The seller updated their profile (new phone) since the row was created.
    db.auth.users["u-seller"] = make_user("u-seller", ["seller"], phone="+15555559999", name="Samuel Seller")

    row = fanxp_accounts.get_or_create_account_seller(db, "u-seller", "49ers")

    assert row["name"] == "Samuel Seller"
    assert row["phone"] == "+15555559999"
    # Still the same row, not a duplicate.
    assert len(db.tables["nfl_sellers"]) == 1
    assert db.tables["nfl_sellers"][0]["phone"] == "+15555559999"


def test_unchanged_profile_does_not_rewrite_the_row(db):
    first = fanxp_accounts.get_or_create_account_seller(db, "u-seller", "49ers")
    created_at = first["created_at"]

    second = fanxp_accounts.get_or_create_account_seller(db, "u-seller", "49ers")

    # No update() call should have fired -- the row (including its
    # creation timestamp) is exactly what it was.
    assert second["created_at"] == created_at


def test_missing_full_name_falls_back_to_a_generic_seller_label(db):
    db.auth.users["u-seller"] = make_user("u-seller", ["seller"], phone="+15555550100", name="")

    row = fanxp_accounts.get_or_create_account_seller(db, "u-seller", "49ers")

    assert row["name"] == "Fan XP seller"
