"""
Tests for the two HTTP-facing "respond" routes built on top of
apply_nfl_response()/resolve_nfl_seat_request():

  - POST /api/nfl/requests/<id>/respond  (respond_nfl_request)
  - GET  /nfl/respond/<id>               (nfl_respond_link)

apply_nfl_response() and resolve_nfl_seat_request() themselves already have
thorough unit coverage in test_fanxp_api.py. What was untested before this
file is the HTTP layer wrapped around them: respond_nfl_request()'s mapping
of apply_nfl_response()'s string error messages to HTTP status codes
(404 / 409 / 400 / 502), and nfl_respond_link()'s branching between six
different rendered-HTML outcomes (not found / expired / already handled /
confirmed / capture failed / declined) -- the actual page a seller sees
after tapping the tap-to-confirm link in their SMS (CLAUDE.md section 6).

Both routes are driven through the real Flask app via app.test_client(),
against a fake Supabase client and a fake Stripe client, so the real
apply_nfl_response()/resolve_nfl_seat_request() code runs underneath --
these tests catch a route-level regression (e.g. a status-code mapping
typo, or a template branch returning the wrong HTML) that purely unit-level
tests of apply_nfl_response() can't see.
"""

from datetime import datetime, timedelta, timezone

import pytest

import fanxp_api
from fanxp_common import NFL_OFFER_WINDOW_MINUTES


# ── Fake Supabase client (same shape as test_fanxp_api.py's) ────────────────

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
        self._single = False
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

    def single(self):
        self._single = True
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

        if self._single:
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
def fake_sms(monkeypatch):
    sent = []
    monkeypatch.setattr(fanxp_api, "send_or_log_sms", lambda twilio, to, body: sent.append((to, body)))
    monkeypatch.setattr(fanxp_api, "get_twilio", lambda: "fake-twilio-client")
    return sent


# ── Fake Stripe client ───────────────────────────────────────────────────────

class _FakePaymentIntent:
    def __init__(self, capture_error=None):
        self._capture_error = capture_error
        self.captured = []
        self.cancelled = []

    def capture(self, intent_id):
        self.captured.append(intent_id)
        if self._capture_error:
            raise self._capture_error

    def cancel(self, intent_id):
        self.cancelled.append(intent_id)


class _FakeStripeClient:
    def __init__(self, capture_error=None):
        self.PaymentIntent = _FakePaymentIntent(capture_error=capture_error)


def stub_stripe(monkeypatch, capture_error=None):
    client = _FakeStripeClient(capture_error=capture_error)
    monkeypatch.setattr(fanxp_api, "get_stripe", lambda: client)
    return client


@pytest.fixture
def client():
    return fanxp_api.app.test_client()


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def make_request(store, **overrides):
    req = {
        "id": 1,
        "status": "seller_pinged",
        "section": "112",
        "row_label": "10",
        "seat_num": "5",
        "fan_phone": "+15550001111",
        "seller_id": 90,
        "created_at": iso(datetime.now(timezone.utc)),
        "stripe_payment_intent_id": None,
    }
    req.update(overrides)
    store.setdefault("nfl_seat_requests", []).append(req)
    return req


def make_seller(store, **overrides):
    seller = {"id": 90, "name": "Jordan Smith", "phone": "+15559998888", "credit_balance": 0}
    seller.update(overrides)
    store.setdefault("nfl_sellers", []).append(seller)
    return seller


def stale_timestamp():
    return iso(datetime.now(timezone.utc) - timedelta(minutes=NFL_OFFER_WINDOW_MINUTES + 5))


# ── POST /api/nfl/requests/<id>/respond ──────────────────────────────────────

def test_respond_missing_decision_returns_400(client, supabase, fake_sms):
    make_request(supabase, id=1)
    make_seller(supabase)

    resp = client.post("/api/nfl/requests/1/respond", json={})

    assert resp.status_code == 400
    assert resp.get_json() == {"error": "decision must be YES or NO"}


def test_respond_unknown_request_id_returns_404(client, supabase, fake_sms):
    resp = client.post("/api/nfl/requests/999/respond", json={"decision": "YES"})

    assert resp.status_code == 404
    assert resp.get_json() == {"error": "not found"}


def test_respond_request_not_awaiting_a_response_returns_409(client, supabase, fake_sms):
    make_request(supabase, id=1, status="confirmed")
    make_seller(supabase)

    resp = client.post("/api/nfl/requests/1/respond", json={"decision": "YES"})

    assert resp.status_code == 409
    assert "not awaiting a response" in resp.get_json()["error"]


def test_respond_yes_confirms_and_returns_200(client, supabase, fake_sms):
    make_request(supabase, id=1, stripe_payment_intent_id=None)
    make_seller(supabase)

    resp = client.post("/api/nfl/requests/1/respond", json={"decision": "yes"})

    assert resp.status_code == 200
    assert resp.get_json() == {"request_id": 1, "status": "confirmed"}
    assert supabase["nfl_seat_requests"][0]["status"] == "confirmed"


def test_respond_no_declines_and_returns_200(client, supabase):
    make_request(supabase, id=1, stripe_payment_intent_id=None)
    make_seller(supabase)

    resp = client.post("/api/nfl/requests/1/respond", json={"decision": "no"})

    assert resp.status_code == 200
    assert resp.get_json() == {"request_id": 1, "status": "declined"}


def test_respond_capture_failure_returns_502(client, supabase, monkeypatch):
    make_request(supabase, id=1, stripe_payment_intent_id="pi_abc123")
    make_seller(supabase)
    stub_stripe(monkeypatch, capture_error=Exception("card declined"))

    resp = client.post("/api/nfl/requests/1/respond", json={"decision": "YES"})

    assert resp.status_code == 502
    assert "payment capture failed" in resp.get_json()["error"]
    # Left pinged, not falsely confirmed.
    assert supabase["nfl_seat_requests"][0]["status"] == "seller_pinged"


# ── GET /nfl/respond/<id> (tap-to-confirm SMS link) ─────────────────────────

def test_link_unknown_request_id_renders_not_found_page(client, supabase):
    resp = client.get("/nfl/respond/999?decision=YES")

    assert resp.status_code == 404
    assert b"Request not found" in resp.data


def test_link_expired_request_renders_expired_page(client, supabase, monkeypatch):
    stub_stripe(monkeypatch)
    make_request(supabase, id=1, status="seller_pinged", created_at=stale_timestamp(),
                 stripe_payment_intent_id="pi_abc123")
    make_seller(supabase)

    resp = client.get("/nfl/respond/1?decision=YES")

    assert resp.status_code == 200
    assert b"This offer expired" in resp.data
    assert supabase["nfl_seat_requests"][0]["status"] == "expired"


def test_link_already_resolved_request_renders_already_handled_page(client, supabase):
    make_request(supabase, id=1, status="confirmed")
    make_seller(supabase)

    resp = client.get("/nfl/respond/1?decision=YES")

    assert resp.status_code == 200
    assert b"Already handled" in resp.data


def test_link_yes_confirms_and_renders_success_page(client, supabase, fake_sms):
    make_request(supabase, id=1, stripe_payment_intent_id=None)
    make_seller(supabase)

    resp = client.get("/nfl/respond/1?decision=YES")

    assert resp.status_code == 200
    assert b"Seat released" in resp.data
    assert supabase["nfl_seat_requests"][0]["status"] == "confirmed"


def test_link_capture_failure_renders_error_page(client, supabase, monkeypatch):
    make_request(supabase, id=1, stripe_payment_intent_id="pi_abc123")
    make_seller(supabase)
    stub_stripe(monkeypatch, capture_error=Exception("card declined"))

    resp = client.get("/nfl/respond/1?decision=YES")

    assert resp.status_code == 502
    assert b"Something went wrong" in resp.data
    assert supabase["nfl_seat_requests"][0]["status"] == "seller_pinged"


def test_link_no_declines_and_renders_kept_page(client, supabase):
    make_request(supabase, id=1, stripe_payment_intent_id=None)
    make_seller(supabase)

    resp = client.get("/nfl/respond/1?decision=NO")

    assert resp.status_code == 200
    assert b"seat kept" in resp.data
    assert supabase["nfl_seat_requests"][0]["status"] == "declined"


def test_link_lowercase_decision_param_is_normalized(client, supabase, fake_sms):
    make_request(supabase, id=1, stripe_payment_intent_id=None)
    make_seller(supabase)

    resp = client.get("/nfl/respond/1?decision=yes")

    assert resp.status_code == 200
    assert b"Seat released" in resp.data
