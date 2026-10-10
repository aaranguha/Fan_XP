"""
Tests for fanxp_api.request_nfl_seat() — the POST /api/nfl/<slug>/request
route a fan hits when claiming an empty seat from docs/nfl_{slug}_seatmap.html.
This is the entry point to the real product flow (CLAUDE.md section 6): it
validates the claim, finds-or-creates the section's seller, inserts the
request row, and opens a Stripe Checkout Session with `capture_method:
manual` so the fan's card is authorized now and only charged later if the
seller confirms.

Had zero test coverage before this file. The things worth protecting here:
  - The four required-field checks, including that `price` uses an explicit
    `is None` check (so a genuinely free/$0 seat isn't rejected as missing).
  - The service-fee math (SERVICE_FEE_RATE) and cents rounding fed into
    Stripe's `unit_amount`, which is real money if ever wrong.
  - The Checkout Session is created with manual capture, card-only payment
    methods, and Managed Payments disabled — each a deliberate choice
    documented inline in fanxp_api.py; a regression here would either charge
    the fan immediately (no seller-confirm step) or silently accept BNPL
    methods/Stripe Tax that were never tested against this flow.
  - success_url/cancel_url/metadata all carry the *new* request's own id,
    which /nfl/respond and the Stripe webhook depend on to find the right row.

Drives the route directly (not via a real HTTP client) inside
`app.test_request_context()`, against a minimal fake Supabase client and a
fake Stripe module, so no real network access or credentials are needed.
"""

from types import SimpleNamespace

import pytest

import fanxp_api
from fanxp_common import SERVICE_FEE_RATE


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

    def select(self, *_args, **_kwargs):
        self._mode = "select"
        return self

    def insert(self, payload):
        self._mode = "insert"
        self._payload = payload
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

    def _matching(self):
        rows = self.store.get(self.table_name, [])
        return [r for r in rows if all(r.get(k) == v for k, v in self.filters.items())]

    def execute(self):
        if self._mode == "insert":
            row = dict(self._payload)
            if "id" not in row:
                existing_ids = [r.get("id", 0) for r in self.store.get(self.table_name, [])]
                row["id"] = (max(existing_ids) + 1) if existing_ids else 1
            self.store.setdefault(self.table_name, []).append(row)
            return FakeResult([row])

        if self._mode == "update":
            matching = self._matching()
            for r in matching:
                r.update(self._payload)
            return FakeResult(matching)

        matching = self._matching()
        limited = matching[: self._limit] if self._limit is not None else matching
        return FakeResult(limited)


class FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return FakeQuery(name, self.store)


@pytest.fixture(autouse=True)
def sth_phone_env(monkeypatch):
    # get_or_create_nfl_seller() raises if this is unset when it needs to
    # create a new seller — set it by default so only the tests about that
    # guard itself need to touch it.
    monkeypatch.setenv("STH_PHONE", "+15559998888")


@pytest.fixture
def store():
    return {}


@pytest.fixture
def fake_supabase(store, monkeypatch):
    sb = FakeSupabase(store)
    monkeypatch.setattr(fanxp_api, "get_supabase", lambda: sb)
    return sb


# ── Fake Stripe ──────────────────────────────────────────────────────────────

@pytest.fixture
def fake_stripe(monkeypatch):
    calls = []

    class _Session:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            n = len(calls)
            return SimpleNamespace(id=f"cs_test_{n}", url=f"https://checkout.stripe.com/pay/cs_test_{n}")

    class _Checkout:
        Session = _Session

    class FakeStripeModule:
        checkout = _Checkout

    monkeypatch.setattr(fanxp_api, "get_stripe", lambda: FakeStripeModule)
    return calls


def post(body):
    with fanxp_api.app.test_request_context(
        "/api/nfl/49ers/request", method="POST", json=body
    ):
        resp = fanxp_api.request_nfl_seat("49ers")
    if isinstance(resp, tuple):
        return resp[0].get_json(), resp[1]
    return resp.get_json(), 200


def valid_body(**overrides):
    body = {
        "section": "112",
        "row": "10",
        "seat": "5",
        "price": 100,
        "fan_name": "Jordan Smith",
        "fan_phone": "+15551234567",
        "return_to_url": "https://fan-xp.vercel.app/nfl_49ers_seatmap.html",
    }
    body.update(overrides)
    return body


# ── Required-field validation ───────────────────────────────────────────────

@pytest.mark.parametrize("field", ["section", "row", "seat", "price"])
def test_missing_seat_identifying_field_returns_400(field, fake_supabase, fake_stripe):
    body = valid_body()
    body[field] = None
    data, status = post(body)

    assert status == 400
    assert "section, row, seat, and price are required" in data["error"]
    assert fake_stripe == []  # never got as far as creating a checkout session


def test_price_of_zero_is_not_treated_as_missing(fake_supabase, fake_stripe):
    # price uses an explicit `is None` check, not a truthiness check, so a
    # free seat must not be rejected as "missing".
    data, status = post(valid_body(price=0))

    assert status == 200
    assert fake_stripe  # checkout session was created


@pytest.mark.parametrize("field", ["fan_name", "fan_phone"])
def test_missing_fan_contact_field_returns_400(field, fake_supabase, fake_stripe):
    data, status = post(valid_body(**{field: ""}))

    assert status == 400
    assert "fan_name and fan_phone are required" in data["error"]
    assert fake_stripe == []


def test_missing_return_to_url_returns_400(fake_supabase, fake_stripe):
    data, status = post(valid_body(return_to_url=""))

    assert status == 400
    assert "return_to_url is required" in data["error"]
    assert fake_stripe == []


# ── Fee / cents math ─────────────────────────────────────────────────────────

def test_fee_and_total_cents_use_the_service_fee_rate(fake_supabase, fake_stripe):
    assert SERVICE_FEE_RATE == 0.12  # pin the documented rate this test's math assumes
    post(valid_body(price=100))

    line_item = fake_stripe[0]["line_items"][0]
    # fee = round(100 * 0.12, 2) = 12.0; total_cents = round((100 + 12.0) * 100)
    assert line_item["price_data"]["unit_amount"] == 11200


def test_fee_rounds_to_two_decimals_before_converting_to_cents(fake_supabase, fake_stripe):
    post(valid_body(price=19.99))

    # fee = round(19.99 * 0.12, 2) = 2.4 ; total_cents = round((19.99 + 2.4) * 100)
    line_item = fake_stripe[0]["line_items"][0]
    assert line_item["price_data"]["unit_amount"] == round((19.99 + round(19.99 * 0.12, 2)) * 100)


# ── Seller dedup via get_or_create_nfl_seller ───────────────────────────────

def test_seller_is_created_once_and_reused_for_the_same_team_and_section(fake_supabase, fake_stripe, store, monkeypatch):
    post(valid_body(section="112"))
    post(valid_body(section="112"))

    assert len(store["nfl_sellers"]) == 1


def test_different_sections_get_different_sellers(fake_supabase, fake_stripe, store, monkeypatch):
    post(valid_body(section="112"))
    post(valid_body(section="220"))

    assert len(store["nfl_sellers"]) == 2


# ── Checkout Session parameters ─────────────────────────────────────────────

def test_checkout_session_uses_manual_capture_card_only_no_managed_payments(fake_supabase, fake_stripe, monkeypatch):
    post(valid_body())

    kwargs = fake_stripe[0]
    assert kwargs["mode"] == "payment"
    assert kwargs["payment_intent_data"] == {"capture_method": "manual"}
    assert kwargs["payment_method_types"] == ["card"]
    assert kwargs["managed_payments"] == {"enabled": False}


def test_line_item_names_the_slug_section_row_and_seat(fake_supabase, fake_stripe, monkeypatch):
    post(valid_body(section="112", row="10", seat="5"))

    product = fake_stripe[0]["line_items"][0]["price_data"]["product_data"]
    assert product["name"] == "49ERS — Sec 112, Row 10, Seat 5"


def test_success_and_cancel_urls_carry_the_new_requests_id(fake_supabase, fake_stripe, store, monkeypatch):
    post(valid_body(return_to_url="https://fan-xp.vercel.app/nfl_49ers_seatmap.html"))

    req_id = store["nfl_seat_requests"][0]["id"]
    kwargs = fake_stripe[0]
    assert kwargs["success_url"] == f"https://fan-xp.vercel.app/nfl_49ers_seatmap.html?request_id={req_id}"
    assert kwargs["cancel_url"] == f"https://fan-xp.vercel.app/nfl_49ers_seatmap.html?cancelled_request_id={req_id}"


def test_metadata_request_id_matches_the_created_row_as_a_string(fake_supabase, fake_stripe, store, monkeypatch):
    post(valid_body())

    req_id = store["nfl_seat_requests"][0]["id"]
    assert fake_stripe[0]["metadata"] == {"request_id": str(req_id)}


# ── Persistence and response shape ──────────────────────────────────────────

def test_request_row_is_linked_to_the_seller_and_updated_with_the_session_id(fake_supabase, fake_stripe, store, monkeypatch):
    post(valid_body(section="112"))

    seller = store["nfl_sellers"][0]
    req = store["nfl_seat_requests"][0]
    assert req["seller_id"] == seller["id"]
    assert req["stripe_session_id"] == "cs_test_1"


def test_response_contains_request_id_and_checkout_url(fake_supabase, fake_stripe, store, monkeypatch):
    data, status = post(valid_body())

    req = store["nfl_seat_requests"][0]
    assert status == 200
    assert data["request_id"] == req["id"]
    assert data["checkout_url"] == "https://checkout.stripe.com/pay/cs_test_1"
