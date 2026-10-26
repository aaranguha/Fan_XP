"""
Tests for fanxp_accounts.py's seller seat-inventory CRUD and release listing:

  - POST   /api/seller/seats         (add_seller_seats)
  - GET    /api/seller/seats         (list_seller_seats)
  - DELETE /api/seller/seats/<id>    (delete_seller_seat)
  - GET    /api/seller/releases      (list_releases)

test_accounts.py's test_add_seats_expands_range_and_blocks_double_registration
only exercises add_seller_seats()'s happy path and its 409 duplicate branch.
Not covered anywhere: its other 400 validation branches (unknown team,
missing section/row, an expand_seats() ValueError, an empty seat list), that
a failed add doesn't insert anything, list_seller_seats()/delete_seller_seat()
at all, and list_releases()'s date filter and its scoping to the caller's
own seller_seats via the seller_seats!inner join (same "404 not 403 for
someone else's row" pattern test_seat_releases_lifecycle.py already
establishes for withdraw_release()).

Reuses test_accounts.py's in-memory Supabase fake and `db`/`call` fixtures,
same as test_seat_releases_lifecycle.py.
"""

from test_accounts import call, db  # noqa: E402,F401


# ── add_seller_seats(): validation ──────────────────────────────────────────

def test_unknown_team_slug_is_rejected(db):
    code, body = call("POST", "/api/seller/seats", token="tok-seller",
                       json={"team_slug": "dodgers", "section": "115", "row": "B", "seats": "5"})
    assert code == 400
    assert "team" in body["error"].lower()
    assert db.tables.get("seller_seats") in (None, [])


def test_missing_section_is_rejected(db):
    code, body = call("POST", "/api/seller/seats", token="tok-seller",
                       json={"team_slug": "giants", "section": "", "row": "B", "seats": "5"})
    assert code == 400
    assert "section" in body["error"].lower()


def test_missing_row_is_rejected(db):
    code, body = call("POST", "/api/seller/seats", token="tok-seller",
                       json={"team_slug": "giants", "section": "115", "row": "", "seats": "5"})
    assert code == 400
    assert "row" in body["error"].lower()


def test_backwards_seat_range_is_rejected_with_expand_seats_message(db):
    code, body = call("POST", "/api/seller/seats", token="tok-seller",
                       json={"team_slug": "giants", "section": "115", "row": "B", "seats": "9-5"})
    assert code == 400
    assert "doesn't look right" in body["error"]


def test_blank_seats_field_is_rejected(db):
    code, body = call("POST", "/api/seller/seats", token="tok-seller",
                       json={"team_slug": "giants", "section": "115", "row": "B", "seats": "   "})
    assert code == 400
    assert "seat number" in body["error"].lower()


def test_rejected_add_does_not_insert_any_rows(db):
    call("POST", "/api/seller/seats", token="tok-seller",
         json={"team_slug": "giants", "section": "115", "row": "B", "seats": "9-5"})
    assert db.tables.get("seller_seats") in (None, [])


def test_partial_overlap_blocks_the_whole_batch(db):
    # One of the two requested seats is already registered -> the whole
    # add is rejected, not a partial insert of just the free one.
    db.tables["seller_seats"] = [{"id": 1, "user_id": "someone-else", "team_slug": "giants",
                                  "section": "115", "row_label": "B", "seat_num": "6"}]
    code, body = call("POST", "/api/seller/seats", token="tok-seller",
                       json={"team_slug": "giants", "section": "115", "row": "b", "seats": "5-6"})
    assert code == 409
    assert "6" in body["error"]
    assert len(db.tables["seller_seats"]) == 1


# ── list_seller_seats() ──────────────────────────────────────────────────────

def test_list_seats_is_empty_for_a_seller_with_none(db):
    code, body = call("GET", "/api/seller/seats", token="tok-seller")
    assert code == 200
    assert body == []


def test_list_seats_only_returns_the_callers_own_seats(db):
    db.tables["seller_seats"] = [
        {"id": 1, "user_id": "u-seller", "team_slug": "giants", "section": "115", "row_label": "B", "seat_num": "5"},
        {"id": 2, "user_id": "someone-else", "team_slug": "giants", "section": "115", "row_label": "B", "seat_num": "6"},
    ]
    code, body = call("GET", "/api/seller/seats", token="tok-seller")
    assert code == 200
    assert [s["id"] for s in body] == [1]
    assert body[0] == {"id": 1, "team_slug": "giants", "section": "115", "row": "B", "seat": "5"}


# ── delete_seller_seat() ─────────────────────────────────────────────────────

def test_delete_unknown_seat_is_404(db):
    code, _ = call("DELETE", "/api/seller/seats/999", token="tok-seller")
    assert code == 404


def test_delete_someone_elses_seat_is_404_not_403(db):
    db.tables["seller_seats"] = [{"id": 1, "user_id": "someone-else", "team_slug": "giants",
                                  "section": "115", "row_label": "B", "seat_num": "5"}]
    code, _ = call("DELETE", "/api/seller/seats/1", token="tok-seller")
    assert code == 404
    assert len(db.tables["seller_seats"]) == 1


def test_delete_own_seat_succeeds_and_removes_the_row(db):
    db.tables["seller_seats"] = [{"id": 1, "user_id": "u-seller", "team_slug": "giants",
                                  "section": "115", "row_label": "B", "seat_num": "5"}]
    code, body = call("DELETE", "/api/seller/seats/1", token="tok-seller")
    assert code == 200
    assert body == {"deleted": 1}
    assert db.tables["seller_seats"] == []


# ── list_releases() ──────────────────────────────────────────────────────────

def _seat(db, seat_id=1, user_id="u-seller"):
    db.tables.setdefault("seller_seats", []).append(
        {"id": seat_id, "user_id": user_id, "team_slug": "giants",
         "section": "115", "row_label": "B", "seat_num": "5"})


def test_list_releases_excludes_past_games(db):
    # db's today_et() is monkeypatched to 2026-10-14 (see test_accounts.py).
    _seat(db)
    db.tables["seat_releases"] = [
        {"id": 1, "seller_seat_id": 1, "game_date": "2026-10-11", "price_usd": 70, "status": "available"},
        {"id": 2, "seller_seat_id": 1, "game_date": "2026-10-18", "price_usd": 80, "status": "available"},
    ]
    code, body = call("GET", "/api/seller/releases", token="tok-seller")
    assert code == 200
    assert [r["id"] for r in body] == [2]


def test_list_releases_only_returns_the_callers_own(db):
    _seat(db, seat_id=1, user_id="u-seller")
    _seat(db, seat_id=2, user_id="someone-else")
    db.tables["seat_releases"] = [
        {"id": 1, "seller_seat_id": 1, "game_date": "2026-10-18", "price_usd": 80, "status": "available"},
        {"id": 2, "seller_seat_id": 2, "game_date": "2026-10-18", "price_usd": 95, "status": "available"},
    ]
    code, body = call("GET", "/api/seller/releases", token="tok-seller")
    assert code == 200
    assert [r["id"] for r in body] == [1]
    assert body[0]["seat"] == "5" and body[0]["price"] == 80.0
