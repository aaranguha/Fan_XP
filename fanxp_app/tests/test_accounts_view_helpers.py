"""
Tests for fanxp_accounts.py's pure view/serialization helpers: _seat_view(),
_release_view(), and session_payload().

These back real, active parts of the NFL seller flow -- GET /api/seller/seats
and GET/POST /api/seller/releases (sell.html reads section/row/seat/price off
their output), and the login/register/refresh response account.html stores in
localStorage. All three are pure dict-shaping functions with no I/O, but
until now none had a direct unit test: the existing route-level tests
(test_accounts.py, test_seat_releases_lifecycle.py) exercise the routes that
call them without ever asserting on the section/row/seat/team_slug/token
fields these helpers are actually responsible for producing -- so a dropped
key or a field-name typo in either function (e.g. "row" vs "row_label") would
currently slip through every existing test untested.
"""

from types import SimpleNamespace

import fanxp_accounts


# ── _seat_view() ─────────────────────────────────────────────────────────────

def test_seat_view_maps_row_label_and_seat_num_to_short_keys():
    row = {"id": 7, "team_slug": "giants", "section": "115",
           "row_label": "B", "seat_num": "5", "user_id": "u-seller"}

    view = fanxp_accounts._seat_view(row)

    assert view == {"id": 7, "team_slug": "giants", "section": "115",
                     "row": "B", "seat": "5"}


def test_seat_view_drops_internal_fields_not_in_its_output_shape():
    # user_id is only used server-side for the ownership check; it must
    # never leak into the seller-facing seat list.
    row = {"id": 1, "team_slug": "jets", "section": "101",
           "row_label": "A", "seat_num": "1", "user_id": "u-secret"}

    view = fanxp_accounts._seat_view(row)

    assert "user_id" not in view


# ── _release_view() ──────────────────────────────────────────────────────────

def _release_row(**overrides):
    row = {
        "id": 3, "game_date": "2026-10-18", "price_usd": "80.00",
        "status": "available", "seller_seat_id": 1,
        "seller_seats": {"team_slug": "giants", "section": "115",
                          "row_label": "B", "seat_num": "5"},
    }
    row.update(overrides)
    return row


def test_release_view_pulls_seat_fields_from_the_embedded_seller_seats_join():
    view = fanxp_accounts._release_view(_release_row())

    assert view == {
        "id": 3, "game_date": "2026-10-18", "price": 80.0,
        "status": "available", "seat_id": 1,
        "team_slug": "giants", "section": "115", "row": "B", "seat": "5",
    }


def test_release_view_converts_price_usd_string_to_float():
    view = fanxp_accounts._release_view(_release_row(price_usd="95"))

    assert view["price"] == 95.0
    assert isinstance(view["price"], float)


def test_release_view_falls_back_to_none_seat_fields_when_join_key_is_absent():
    # Defensive: if the embedded seller_seats resource is ever missing
    # (e.g. a row built without the RELEASE_SELECT join), the seat fields
    # must come back None rather than raising an AttributeError/KeyError.
    row = _release_row()
    del row["seller_seats"]

    view = fanxp_accounts._release_view(row)

    assert view["team_slug"] is None
    assert view["section"] is None
    assert view["row"] is None
    assert view["seat"] is None


def test_release_view_falls_back_to_none_seat_fields_when_join_is_explicitly_none():
    view = fanxp_accounts._release_view(_release_row(seller_seats=None))

    assert view["team_slug"] is None
    assert view["seat"] is None


# ── session_payload() ────────────────────────────────────────────────────────

def _user(roles=("buyer",), phone="+15555550100"):
    return SimpleNamespace(
        id="u-1", email="sam@example.com",
        user_metadata={"full_name": "Sam Seller", "phone": phone, "roles": list(roles)},
    )


def test_session_payload_carries_tokens_and_expiry_verbatim():
    session = SimpleNamespace(access_token="tok-a", refresh_token="tok-r", expires_at=1234567890)

    payload = fanxp_accounts.session_payload(session, _user())

    assert payload["access_token"] == "tok-a"
    assert payload["refresh_token"] == "tok-r"
    assert payload["expires_at"] == 1234567890


def test_session_payload_embeds_the_users_profile_fields():
    session = SimpleNamespace(access_token="tok-a", refresh_token="tok-r", expires_at=1)

    payload = fanxp_accounts.session_payload(session, _user(roles=["buyer", "seller"]))

    assert payload["user"]["id"] == "u-1"
    assert payload["user"]["email"] == "sam@example.com"
    assert payload["user"]["full_name"] == "Sam Seller"
    assert payload["user"]["roles"] == ["buyer", "seller"]


def test_session_payload_does_not_leak_the_raw_user_metadata_object():
    session = SimpleNamespace(access_token="tok-a", refresh_token="tok-r", expires_at=1)

    payload = fanxp_accounts.session_payload(session, _user())

    assert "user_metadata" not in payload["user"]
