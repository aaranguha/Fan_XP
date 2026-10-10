"""
Tests for fanxp_api.ping_nfl_seller() — texts the seller once the fan's
Stripe card hold is authorized, and flips the request to 'seller_pinged'.
Called only from the Stripe webhook (per its docstring), so it always runs
inside an active Flask request context in production.

This had zero test coverage before this file: nothing exercised its
seller lookup (by `req["seller_id"]`, not the request's own id -- a wrong
key here would text the wrong season ticket holder), its API_PUBLIC_URL
override of `request.url_root` when building the tap-to-confirm links
baked into the SMS (see fanxp_common.send_nfl_seller_sms), or that the
request row is updated with the real message SID rather than some
placeholder, which `nfl_request_status`/`apply_nfl_response` rely on to
decide the request is awaiting a reply.

Drives the function against a minimal fake Supabase client and a stubbed
send_nfl_seller_sms(), so no real network access or credentials are
needed. The one real network-adjacent dependency, get_twilio(), is also
stubbed since ping_nfl_seller never inspects its return value beyond
passing it through.
"""

import pytest

import fanxp_api
from fanxp_common import CREDIT_OFFER


# ── Fake Supabase client ─────────────────────────────────────────────────────

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
def sb(store):
    return FakeSupabase(store)


@pytest.fixture
def fake_sms(monkeypatch):
    """Stubs the SMS-sending step one level down, so these tests cover only
    ping_nfl_seller's own orchestration (lookup -> send -> update)."""
    sent = []

    class FakeMessage:
        def __init__(self, sid):
            self.sid = sid

    def fake_send(twilio, seller, req, api_base_url, credit_offer=CREDIT_OFFER):
        sent.append({
            "twilio": twilio,
            "seller": seller,
            "req": req,
            "api_base_url": api_base_url,
            "credit_offer": credit_offer,
        })
        return FakeMessage(f"SM{len(sent)}")

    monkeypatch.setattr(fanxp_api, "send_nfl_seller_sms", fake_send)
    monkeypatch.setattr(fanxp_api, "get_twilio", lambda: "fake-twilio-client")
    return sent


def make_request(store, **overrides):
    req = {
        "id": 1,
        "status": "requested",
        "section": "112",
        "row_label": "10",
        "seat_num": "5",
        "seller_id": 90,
    }
    req.update(overrides)
    store.setdefault("nfl_seat_requests", []).append(req)
    return req


def make_seller(store, **overrides):
    seller = {"id": 90, "name": "Jordan Smith", "phone": "+15559998888"}
    seller.update(overrides)
    store.setdefault("nfl_sellers", []).append(seller)
    return seller


# ── Seller lookup is keyed off req["seller_id"], not the request's own id ──

def test_looks_up_the_seller_by_seller_id_not_request_id(sb, store, fake_sms, monkeypatch):
    monkeypatch.delenv("API_PUBLIC_URL", raising=False)
    # Deliberately give the request and its seller different ids so a
    # lookup bug (e.g. keying off req["id"] instead of req["seller_id"])
    # would pick the wrong row or find nothing.
    req = make_request(store, id=7, seller_id=90)
    seller = make_seller(store, id=90)
    make_seller(store, id=7, name="Wrong Seller")  # same id as the request, must not be used

    with fanxp_api.app.test_request_context():
        fanxp_api.ping_nfl_seller(sb, req)

    assert fake_sms[0]["seller"] == seller


# ── API_PUBLIC_URL override vs request.url_root fallback ──────────────────

def test_api_public_url_env_var_is_used_when_set(sb, store, fake_sms, monkeypatch):
    monkeypatch.setenv("API_PUBLIC_URL", "https://fanxp-api.onrender.com")
    req = make_request(store)
    make_seller(store)

    with fanxp_api.app.test_request_context():
        fanxp_api.ping_nfl_seller(sb, req)

    assert fake_sms[0]["api_base_url"] == "https://fanxp-api.onrender.com"


def test_falls_back_to_request_url_root_when_api_public_url_unset(sb, store, fake_sms, monkeypatch):
    monkeypatch.delenv("API_PUBLIC_URL", raising=False)
    req = make_request(store)
    make_seller(store)

    with fanxp_api.app.test_request_context():
        fanxp_api.ping_nfl_seller(sb, req)

    assert fake_sms[0]["api_base_url"] == "http://localhost/"


# ── Credit offer is passed through unchanged ───────────────────────────────

def test_credit_offer_constant_is_passed_through(sb, store, fake_sms, monkeypatch):
    monkeypatch.setenv("API_PUBLIC_URL", "https://example.test")
    req = make_request(store)
    make_seller(store)

    with fanxp_api.app.test_request_context():
        fanxp_api.ping_nfl_seller(sb, req)

    assert fake_sms[0]["credit_offer"] == CREDIT_OFFER


# ── Request row is updated correctly, and only that row ───────────────────

def test_request_status_flips_to_seller_pinged_with_the_real_message_sid(sb, store, fake_sms, monkeypatch):
    monkeypatch.setenv("API_PUBLIC_URL", "https://example.test")
    req = make_request(store, id=42, status="requested")
    make_seller(store)

    with fanxp_api.app.test_request_context():
        fanxp_api.ping_nfl_seller(sb, req)

    updated = store["nfl_seat_requests"][0]
    assert updated["status"] == "seller_pinged"
    assert updated["message_sid"] == "SM1"


def test_only_the_matching_request_row_is_updated(sb, store, fake_sms, monkeypatch):
    monkeypatch.setenv("API_PUBLIC_URL", "https://example.test")
    target = make_request(store, id=1, seller_id=90, status="requested")
    other = make_request(store, id=2, seller_id=90, status="requested")
    make_seller(store, id=90)

    with fanxp_api.app.test_request_context():
        fanxp_api.ping_nfl_seller(sb, target)

    assert store["nfl_seat_requests"][0]["status"] == "seller_pinged"
    assert store["nfl_seat_requests"][1]["status"] == "requested"
