"""
Tests for fanxp_accounts.py's seller request inbox and web respond flow:

  - GET  /api/seller/requests               (seller_requests / _my_seller_ids)
  - POST /api/seller/requests/<id>/respond  (seller_respond)
  - GET  /api/me/requests                   (my_requests)
  - the pure view helpers _request_view()/_seat_view() they share

These are the account-web-UI equivalents of the SMS tap-to-confirm flow that
test_nfl_respond_routes.py and test_fanxp_api.py already cover for
apply_nfl_response()/resolve_nfl_seat_request() themselves -- what was
untested before this file is the auth-scoping layer on top: a seller must
only ever see or act on requests for their own nfl_sellers row(s), a
checkout-only "requested" row isn't a real request yet, and a buyer's own
request history hides the gate pass code until it's actually confirmed.
Runs against an in-memory fake Supabase client; no network or credentials.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import fanxp_accounts
import fanxp_api


# ── In-memory Supabase fake (same shape as test_accounts.py's) ─────────────

class Q:
    def __init__(self, db, table):
        self.db, self.table, self.f, self.mode = db, table, [], "select"
        self.payload = None
        self._single = False
        self._maybe_single = False

    def _rows(self):
        return self.db.tables.setdefault(self.table, [])

    def select(self, *a, **k):
        return self

    def update(self, payload):
        self.mode, self.payload = "update", payload
        return self

    def eq(self, k, v):
        self.f.append((k, lambda x, v=v: str(x) == str(v)))
        return self

    def neq(self, k, v):
        self.f.append((k, lambda x, v=v: str(x) != str(v)))
        return self

    def in_(self, k, vs):
        self.f.append((k, lambda x, vs=vs: x in vs))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def single(self):
        self._single = True
        return self

    def maybe_single(self):
        self._maybe_single = True
        return self

    def _match(self):
        return [r for r in self._rows() if all(fn(r.get(k)) for k, fn in self.f)]

    def execute(self):
        matched = self._match()
        if self.mode == "update":
            ids = {r["id"] for r in matched}
            for r in self._rows():
                if r["id"] in ids:
                    r.update(self.payload)
            return SimpleNamespace(data=[r for r in self._rows() if r["id"] in ids])
        if self._maybe_single:
            # The documented postgrest quirk: zero rows -> None itself, not
            # a result object with `.data = None` (see fetch_one()).
            return SimpleNamespace(data=matched[0]) if matched else None
        if self._single:
            return SimpleNamespace(data=matched[0])
        return SimpleNamespace(data=matched)


class FakeAuth:
    def __init__(self, users):
        self.users, self.admin = users, self

    def get_user(self, token):
        user = self.users.get(token)
        if not user:
            raise RuntimeError("invalid JWT")
        return SimpleNamespace(user=user)


class FakeDB:
    def __init__(self, users=None):
        self.tables = {}
        self.auth = FakeAuth(users or {})

    def table(self, name):
        return Q(self, name)


def make_user(uid, roles, phone="+15555550100", name="Sam Seller"):
    return SimpleNamespace(id=uid, email=f"{uid}@x.com",
                           user_metadata={"full_name": name, "phone": phone, "roles": roles})


@pytest.fixture
def db(monkeypatch):
    users = {
        "tok-seller1": make_user("u-seller1", ["seller"], name="Sam Seller"),
        "tok-seller2": make_user("u-seller2", ["seller"], name="Sal Seller"),
        "tok-buyer": make_user("u-buyer", ["buyer"], phone="+15555550199", name="Bea Buyer"),
    }
    fake = FakeDB(users)
    monkeypatch.setattr(fanxp_accounts, "get_supabase", lambda: fake)
    monkeypatch.setattr(fanxp_api, "get_supabase", lambda: fake)
    return fake


@pytest.fixture
def fake_sms(monkeypatch):
    sent = []
    monkeypatch.setattr(fanxp_api, "send_or_log_sms", lambda twilio, to, body: sent.append((to, body)))
    monkeypatch.setattr(fanxp_api, "get_twilio", lambda: "fake-twilio-client")
    return sent


def call(method, path, token=None, json=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    client = fanxp_api.app.test_client()
    resp = client.open(path, method=method, json=json, headers=headers)
    return resp.status_code, resp.get_json()


def make_seller_row(db, **overrides):
    row = {"id": 1, "user_id": "u-seller1", "name": "Sam Seller",
           "phone": "+15555550100", "credit_balance": 0}
    row.update(overrides)
    db.tables.setdefault("nfl_sellers", []).append(row)
    return row


def make_request_row(db, **overrides):
    # created_at defaults to "now" so a plain "seller_pinged" row isn't
    # flipped to "expired" by expire_if_stale() (NFL_OFFER_WINDOW_MINUTES)
    # before the test gets to act on it.
    row = {"id": 1, "team_slug": "giants", "section": "115", "row_label": "B", "seat_num": "5",
           "price_usd": 90.0, "status": "seller_pinged", "fan_name": "Fan One",
           "fan_phone": "+15550001111", "seller_id": 1, "buyer_user_id": None,
           "created_at": datetime.now(timezone.utc).isoformat(), "stripe_payment_intent_id": None}
    row.update(overrides)
    db.tables.setdefault("nfl_seat_requests", []).append(row)
    return row


# ── _request_view() / _seat_view(): pure, no I/O ────────────────────────────

def test_request_view_shows_pass_code_only_when_confirmed():
    row = {"id": 1, "team_slug": "giants", "section": "115", "row_label": "B", "seat_num": "5",
           "price_usd": "90.0", "status": "confirmed", "created_at": "2026-10-14T00:00:00Z",
           "pass_code": "ABC123"}
    view = fanxp_accounts._request_view(row)
    assert view["pass_code"] == "ABC123"
    assert view["price"] == 90.0  # coerced from the string PostgREST returns


def test_request_view_hides_pass_code_when_not_confirmed():
    row = {"id": 1, "team_slug": "giants", "section": "115", "row_label": "B", "seat_num": "5",
           "price_usd": 90.0, "status": "seller_pinged", "created_at": "2026-10-14T00:00:00Z",
           "pass_code": "ABC123"}
    # Shouldn't happen in practice (pass_code is only ever set alongside
    # status=confirmed), but the view must gate on status, not presence.
    assert "pass_code" not in fanxp_accounts._request_view(row)


# ── _my_seller_ids() ─────────────────────────────────────────────────────────

def test_my_seller_ids_returns_only_this_users_rows(db):
    make_seller_row(db, id=1, user_id="u-seller1")
    make_seller_row(db, id=2, user_id="u-seller2")
    make_seller_row(db, id=3, user_id="u-seller1")
    assert sorted(fanxp_accounts._my_seller_ids(db, "u-seller1")) == [1, 3]
    assert fanxp_accounts._my_seller_ids(db, "u-nobody") == []


# ── GET /api/seller/requests ─────────────────────────────────────────────────

def test_seller_requests_needs_sign_in_and_seller_role(db):
    assert call("GET", "/api/seller/requests")[0] == 401
    assert call("GET", "/api/seller/requests", token="tok-buyer")[0] == 403


def test_seller_requests_with_no_seller_rows_returns_empty(db):
    code, body = call("GET", "/api/seller/requests", token="tok-seller1")
    assert code == 200 and body == []


def test_seller_requests_only_sees_own_sellers_requests(db):
    make_seller_row(db, id=1, user_id="u-seller1")
    make_seller_row(db, id=2, user_id="u-seller2")
    make_request_row(db, id=10, seller_id=1)
    make_request_row(db, id=11, seller_id=2)

    code, body = call("GET", "/api/seller/requests", token="tok-seller1")
    assert code == 200
    assert [r["id"] for r in body] == [10]


def test_seller_requests_excludes_checkout_only_rows(db):
    make_seller_row(db, id=1, user_id="u-seller1")
    make_request_row(db, id=10, seller_id=1, status="requested")  # abandoned/mid checkout
    make_request_row(db, id=11, seller_id=1, status="seller_pinged")
    make_request_row(db, id=12, seller_id=1, status="confirmed", pass_code="XYZ")
    make_request_row(db, id=13, seller_id=1, status="declined")

    code, body = call("GET", "/api/seller/requests", token="tok-seller1")
    assert code == 200
    assert sorted(r["id"] for r in body) == [11, 12, 13]


def test_seller_requests_strips_pass_code_and_adds_first_name(db):
    make_seller_row(db, id=1, user_id="u-seller1")
    make_request_row(db, id=10, seller_id=1, status="confirmed",
                      pass_code="SECRET1", fan_name="Bea Buyer")

    code, body = call("GET", "/api/seller/requests", token="tok-seller1")
    assert code == 200
    assert "pass_code" not in body[0]
    assert body[0]["fan_first_name"] == "Bea"


# ── POST /api/seller/requests/<id>/respond ───────────────────────────────────

def test_seller_respond_needs_sign_in_and_seller_role(db):
    assert call("POST", "/api/seller/requests/1/respond", json={"decision": "YES"})[0] == 401
    assert call("POST", "/api/seller/requests/1/respond", token="tok-buyer",
                json={"decision": "YES"})[0] == 403


def test_seller_respond_404s_for_request_owned_by_a_different_seller(db):
    make_seller_row(db, id=1, user_id="u-seller1")
    make_seller_row(db, id=2, user_id="u-seller2")
    make_request_row(db, id=10, seller_id=2)

    code, body = call("POST", "/api/seller/requests/10/respond", token="tok-seller1",
                      json={"decision": "YES"})
    assert code == 404 and body["error"] == "not found"


def test_seller_respond_404s_when_caller_has_no_seller_rows_at_all(db):
    make_request_row(db, id=10, seller_id=1)
    code, _ = call("POST", "/api/seller/requests/10/respond", token="tok-seller1",
                  json={"decision": "YES"})
    assert code == 404


def test_seller_respond_rejects_bad_decision(db):
    make_seller_row(db, id=1, user_id="u-seller1")
    make_request_row(db, id=10, seller_id=1)
    code, body = call("POST", "/api/seller/requests/10/respond", token="tok-seller1",
                      json={"decision": "MAYBE"})
    assert code == 400 and "YES or NO" in body["error"]


def test_seller_respond_409s_when_not_awaiting_a_response(db):
    make_seller_row(db, id=1, user_id="u-seller1")
    make_request_row(db, id=10, seller_id=1, status="confirmed")
    code, body = call("POST", "/api/seller/requests/10/respond", token="tok-seller1",
                      json={"decision": "YES"})
    assert code == 409 and "not awaiting a response" in body["error"]


def test_seller_respond_yes_confirms_and_returns_200(db, fake_sms):
    make_seller_row(db, id=1, user_id="u-seller1", credit_balance=0)
    make_request_row(db, id=10, seller_id=1, stripe_payment_intent_id=None)

    code, body = call("POST", "/api/seller/requests/10/respond", token="tok-seller1",
                      json={"decision": "yes"})

    assert code == 200 and body == {"request_id": 10, "status": "confirmed"}
    req = next(r for r in db.tables["nfl_seat_requests"] if r["id"] == 10)
    assert req["status"] == "confirmed" and req["pass_code"]


def test_seller_respond_no_declines_and_returns_200(db, fake_sms):
    make_seller_row(db, id=1, user_id="u-seller1")
    make_request_row(db, id=10, seller_id=1, stripe_payment_intent_id=None)

    code, body = call("POST", "/api/seller/requests/10/respond", token="tok-seller1",
                      json={"decision": "NO"})

    assert code == 200 and body == {"request_id": 10, "status": "declined"}


def test_seller_respond_502s_on_capture_failure_and_leaves_request_pinged(db, fake_sms, monkeypatch):
    make_seller_row(db, id=1, user_id="u-seller1")
    make_request_row(db, id=10, seller_id=1, stripe_payment_intent_id="pi_abc123")

    def boom(intent_id):
        raise RuntimeError("card declined")
    monkeypatch.setattr(fanxp_api, "get_stripe",
                        lambda: SimpleNamespace(PaymentIntent=SimpleNamespace(capture=boom)))

    code, body = call("POST", "/api/seller/requests/10/respond", token="tok-seller1",
                      json={"decision": "YES"})

    assert code == 502 and "capture failed" in body["error"]
    req = next(r for r in db.tables["nfl_seat_requests"] if r["id"] == 10)
    assert req["status"] == "seller_pinged"


# ── GET /api/me/requests ──────────────────────────────────────────────────────

def test_my_requests_needs_sign_in(db):
    assert call("GET", "/api/me/requests")[0] == 401


def test_my_requests_only_returns_this_buyers_own_requests(db):
    make_request_row(db, id=10, buyer_user_id="u-buyer")
    make_request_row(db, id=11, buyer_user_id="someone-else")

    code, body = call("GET", "/api/me/requests", token="tok-buyer")
    assert code == 200
    assert [r["id"] for r in body] == [10]


def test_my_requests_shows_pass_code_only_once_confirmed(db):
    make_request_row(db, id=10, buyer_user_id="u-buyer", status="seller_pinged")
    make_request_row(db, id=11, buyer_user_id="u-buyer", status="confirmed", pass_code="GATE1")

    code, body = call("GET", "/api/me/requests", token="tok-buyer")
    assert code == 200
    by_id = {r["id"]: r for r in body}
    assert "pass_code" not in by_id[10]
    assert by_id[11]["pass_code"] == "GATE1"
