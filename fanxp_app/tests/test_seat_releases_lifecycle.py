"""
Tests for fanxp_accounts.py's seat-release lifecycle: create_releases()
(POST /api/seller/releases) and withdraw_release() (DELETE
/api/seller/releases/<id>).

Not covered by test_accounts.py, which only checks the ownership (403) and
past-date (400) branches of create_releases() in passing while testing the
claim flow. This file covers the rest of create_releases()'s validation
(bad price, malformed date, empty seat list), the upsert-based re-release
path (releasing the same seat for the same game twice must update the one
row, not create a duplicate -- `seat_releases` is upserted on
`seller_seat_id,game_date`), and withdraw_release()'s whole status machine
(not found, not yours, already sold/withdrawn, and the real withdrawal).

Reuses test_accounts.py's in-memory Supabase fake and `db`/`call` fixtures
rather than re-implementing them, matching the pattern already used by
scraping/tests/test_check_data_integrity_pagination.py.
"""

from test_accounts import call, db  # noqa: E402,F401


# ── create_releases(): validation ───────────────────────────────────────────

def _seed_one_seat(db, seat_id=1, user_id="u-seller"):
    db.tables["seller_seats"] = [{"id": seat_id, "user_id": user_id, "team_slug": "giants",
                                  "section": "115", "row_label": "B", "seat_num": "5"}]


def test_missing_price_is_rejected(db):
    _seed_one_seat(db)
    code, body = call("POST", "/api/seller/releases", token="tok-seller",
                       json={"seat_ids": [1], "game_date": "2026-10-18"})
    assert code == 400
    assert "price" in body["error"].lower()


def test_non_numeric_price_is_rejected(db):
    _seed_one_seat(db)
    code, body = call("POST", "/api/seller/releases", token="tok-seller",
                       json={"seat_ids": [1], "game_date": "2026-10-18", "price": "free"})
    assert code == 400
    assert "price" in body["error"].lower()


def test_zero_or_negative_price_is_rejected(db):
    _seed_one_seat(db)
    for bad_price in (0, -5):
        code, body = call("POST", "/api/seller/releases", token="tok-seller",
                           json={"seat_ids": [1], "game_date": "2026-10-18", "price": bad_price})
        assert code == 400
        assert "$1" in body["error"]


def test_price_over_5000_is_rejected(db):
    _seed_one_seat(db)
    code, body = call("POST", "/api/seller/releases", token="tok-seller",
                       json={"seat_ids": [1], "game_date": "2026-10-18", "price": 5000.01})
    assert code == 400
    assert "$5,000" in body["error"]


def test_price_of_exactly_5000_is_accepted(db):
    _seed_one_seat(db)
    code, _ = call("POST", "/api/seller/releases", token="tok-seller",
                   json={"seat_ids": [1], "game_date": "2026-10-18", "price": 5000})
    assert code == 201


def test_malformed_game_date_is_rejected(db):
    _seed_one_seat(db)
    code, body = call("POST", "/api/seller/releases", token="tok-seller",
                       json={"seat_ids": [1], "game_date": "10/18/2026", "price": 80})
    assert code == 400
    assert "date" in body["error"].lower()


def test_empty_seat_list_is_rejected(db):
    code, body = call("POST", "/api/seller/releases", token="tok-seller",
                       json={"seat_ids": [], "game_date": "2026-10-18", "price": 80})
    assert code == 400
    assert "seat" in body["error"].lower()


def test_non_list_seat_ids_is_rejected(db):
    code, body = call("POST", "/api/seller/releases", token="tok-seller",
                       json={"seat_ids": 1, "game_date": "2026-10-18", "price": 80})
    assert code == 400
    assert "seat" in body["error"].lower()


# ── create_releases(): the upsert re-release path ───────────────────────────

def test_releasing_the_same_seat_and_game_twice_updates_the_existing_row_only(db):
    _seed_one_seat(db)
    call("POST", "/api/seller/releases", token="tok-seller",
         json={"seat_ids": [1], "game_date": "2026-10-18", "price": 80})
    code, body = call("POST", "/api/seller/releases", token="tok-seller",
                       json={"seat_ids": [1], "game_date": "2026-10-18", "price": 95})

    assert code == 201
    releases = db.tables["seat_releases"]
    assert len(releases) == 1
    assert releases[0]["price_usd"] == 95
    assert body[0]["price"] == 95.0


def test_releasing_the_same_seat_for_a_different_game_is_a_separate_row(db):
    _seed_one_seat(db)
    call("POST", "/api/seller/releases", token="tok-seller",
         json={"seat_ids": [1], "game_date": "2026-10-18", "price": 80})
    call("POST", "/api/seller/releases", token="tok-seller",
         json={"seat_ids": [1], "game_date": "2026-10-25", "price": 85})

    assert len(db.tables["seat_releases"]) == 2


# ── withdraw_release() ───────────────────────────────────────────────────────

def _seed_release(db, release_id=1, seat_id=1, status="available", user_id="u-seller"):
    _seed_one_seat(db, seat_id=seat_id, user_id=user_id)
    db.tables["seat_releases"] = [{"id": release_id, "seller_seat_id": seat_id,
                                   "game_date": "2026-10-18", "price_usd": 80, "status": status}]


def test_withdraw_unknown_release_is_404(db):
    _seed_release(db)
    code, body = call("DELETE", "/api/seller/releases/999", token="tok-seller")
    assert code == 404


def test_withdraw_someone_elses_release_is_404_not_403(db):
    # The lookup is scoped by a join on seller_seats.user_id, so a release
    # that isn't the caller's own doesn't even reveal that it exists.
    _seed_release(db, user_id="someone-else")
    code, body = call("DELETE", "/api/seller/releases/1", token="tok-seller")
    assert code == 404


def test_withdraw_already_withdrawn_release_is_409(db):
    _seed_release(db, status="withdrawn")
    code, body = call("DELETE", "/api/seller/releases/1", token="tok-seller")
    assert code == 409
    assert "withdrawn" in body["error"]


def test_withdraw_a_sold_release_is_409_and_says_sold(db):
    _seed_release(db, status="sold")
    code, body = call("DELETE", "/api/seller/releases/1", token="tok-seller")
    assert code == 409
    assert "sold" in body["error"]


def test_withdraw_an_available_release_succeeds_and_flips_status(db):
    _seed_release(db, status="available")
    code, body = call("DELETE", "/api/seller/releases/1", token="tok-seller")
    assert code == 200
    assert body == {"id": 1, "status": "withdrawn"}
    assert db.tables["seat_releases"][0]["status"] == "withdrawn"


def test_withdrawing_twice_fails_the_second_time(db):
    _seed_release(db, status="available")
    call("DELETE", "/api/seller/releases/1", token="tok-seller")
    code, body = call("DELETE", "/api/seller/releases/1", token="tok-seller")
    assert code == 409
