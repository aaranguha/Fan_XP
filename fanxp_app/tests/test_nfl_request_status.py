"""
Tests for GET /api/nfl/requests/<id> (nfl_request_status) -- the route the
NFL seatmap page polls after Stripe checkout to show the fan their request's
current status.

This route had zero test coverage before this file. It is a thin wrapper
around two already-tested pieces (fetch_one() and expire_if_stale(), both
covered in test_fanxp_api.py), but the wrapper itself has logic that was
never exercised end-to-end:

  - the 404-on-missing-row branch
  - that it actually calls expire_if_stale() before building the response,
    so a stale 'seller_pinged'/'requested' row is reported as 'expired' to
    the polling fan rather than a now-wrong stale status
  - the field renames (row_label -> row, seat_num -> seat) and the
    price_usd -> float coercion
  - the conditional: pass_code is only ever included in the response when
    status == 'confirmed' AND the row actually has a pass_code -- a
    'confirmed' row with no pass_code (shouldn't happen, but the code
    guards it) or any non-confirmed row must never leak a pass_code key.

Driven through the real Flask app via app.test_client() against a fake
Supabase client (and a fake Stripe client for the expiry-cancels-the-hold
path), so the real expire_if_stale()/fetch_one() code runs underneath.
"""

from datetime import datetime, timedelta, timezone

import pytest

import fanxp_api
from fanxp_common import NFL_OFFER_WINDOW_MINUTES


# ── Fake Supabase client (same shape as test_nfl_respond_routes.py's) ──────

class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, table_name, store):
        self.table_name = table_name
        self.store = store
        self.filters = {}
        self._limit = None
        self._mode = "select"
        self._payload = None
        self._maybe_single = False

    def select(self, *_args, **_kwargs):
        self._mode = "select"
        return self

    def update(self, payload):
        self._mode = "update"
        self._payload = payload
        return self

    def eq(self, field, value):
        self.filters[field] = value
        return self

    def limit(self, n):
        self._limit = n
        return self

    def maybe_single(self):
        self._maybe_single = True
        return self

    def _matching(self):
        rows = self.store.get(self.table_name, [])
        return [r for r in rows if all(r.get(k) == v for k, v in self.filters.items())]

    def execute(self):
        if self._mode == "update":
            matching = self._matching()
            for r in matching:
                r.update(self._payload)
            return FakeResult(matching)

        matching = self._matching()
        limited = matching[: self._limit] if self._limit is not None else matching

        if self._maybe_single:
            # The documented postgrest quirk: zero rows -> None itself, not
            # a result object with `.data = None`.
            if not limited:
                return None
            return FakeResult(limited[0])

        return FakeResult(limited)


class FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return FakeQuery(name, self.store)


@pytest.fixture
def store():
    return {}


@pytest.fixture
def supabase(monkeypatch, store):
    monkeypatch.setattr(fanxp_api, "get_supabase", lambda: FakeSupabase(store))
    return store


@pytest.fixture
def client():
    return fanxp_api.app.test_client()


class _FakePaymentIntent:
    def __init__(self):
        self.cancelled = []

    def cancel(self, intent_id):
        self.cancelled.append(intent_id)


class _FakeStripeClient:
    def __init__(self):
        self.PaymentIntent = _FakePaymentIntent()


@pytest.fixture
def fake_stripe(monkeypatch):
    stripe_client = _FakeStripeClient()
    monkeypatch.setattr(fanxp_api, "get_stripe", lambda: stripe_client)
    return stripe_client


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def make_request(store, **overrides):
    req = {
        "id": 1,
        "status": "seller_pinged",
        "section": "112",
        "row_label": "10",
        "seat_num": "5",
        "price_usd": "45",
        "fan_phone": "+15550001111",
        "seller_id": 90,
        "created_at": iso(datetime.now(timezone.utc)),
        "stripe_payment_intent_id": None,
    }
    req.update(overrides)
    store.setdefault("nfl_seat_requests", []).append(req)
    return req


def stale_timestamp():
    return iso(datetime.now(timezone.utc) - timedelta(minutes=NFL_OFFER_WINDOW_MINUTES + 5))


# ── 404 ──────────────────────────────────────────────────────────────────

def test_unknown_request_id_returns_404(client, supabase):
    resp = client.get("/api/nfl/requests/999")

    assert resp.status_code == 404
    assert resp.get_json() == {"error": "not found"}


# ── Field mapping ────────────────────────────────────────────────────────

def test_response_renames_and_coerces_fields(client, supabase):
    make_request(
        supabase, id=1, status="seller_pinged",
        section="112", row_label="10", seat_num="5", price_usd="45",
    )

    resp = client.get("/api/nfl/requests/1")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["request_id"] == 1
    assert body["status"] == "seller_pinged"
    assert body["section"] == "112"
    assert body["row"] == "10"
    assert body["seat"] == "5"
    assert body["price"] == 45.0
    assert isinstance(body["price"], float)


# ── pass_code visibility ─────────────────────────────────────────────────

def test_confirmed_request_with_pass_code_includes_it(client, supabase):
    make_request(supabase, id=1, status="confirmed", pass_code="ABC123")

    resp = client.get("/api/nfl/requests/1")

    assert resp.get_json()["pass_code"] == "ABC123"


def test_non_confirmed_request_never_includes_a_pass_code_even_if_set(client, supabase):
    # Defensive: a stray pass_code on a not-yet-confirmed row must never
    # leak to a polling fan before the seller has actually said yes.
    make_request(supabase, id=1, status="seller_pinged", pass_code="LEAKED")

    resp = client.get("/api/nfl/requests/1")

    assert "pass_code" not in resp.get_json()


def test_confirmed_request_without_a_pass_code_omits_the_key(client, supabase):
    make_request(supabase, id=1, status="confirmed", pass_code=None)

    resp = client.get("/api/nfl/requests/1")

    assert "pass_code" not in resp.get_json()


# ── Lazy expiry sweep integration ────────────────────────────────────────

def test_stale_seller_pinged_request_is_reported_as_expired(client, supabase, fake_stripe):
    make_request(
        supabase, id=1, status="seller_pinged", created_at=stale_timestamp(),
        stripe_payment_intent_id="pi_abc123",
    )

    resp = client.get("/api/nfl/requests/1")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "expired"
    # The stored row was actually flipped too, not just the response.
    assert supabase["nfl_seat_requests"][0]["status"] == "expired"
    # The payment hold is released as part of expiring it.
    assert fake_stripe.PaymentIntent.cancelled == ["pi_abc123"]


def test_fresh_seller_pinged_request_is_not_expired(client, supabase, fake_stripe):
    make_request(supabase, id=1, status="seller_pinged", created_at=iso(datetime.now(timezone.utc)))

    resp = client.get("/api/nfl/requests/1")

    assert resp.get_json()["status"] == "seller_pinged"
    assert fake_stripe.PaymentIntent.cancelled == []


def test_stale_but_terminal_status_is_left_alone(client, supabase, fake_stripe):
    # 'confirmed'/'declined' are terminal -- age must never flip them, even
    # if created_at is ancient.
    make_request(
        supabase, id=1, status="confirmed", created_at=stale_timestamp(),
        pass_code="ABC123",
    )

    resp = client.get("/api/nfl/requests/1")

    body = resp.get_json()
    assert body["status"] == "confirmed"
    assert body["pass_code"] == "ABC123"
    assert fake_stripe.PaymentIntent.cancelled == []
