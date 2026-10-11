"""
Tests for fanxp_api.stripe_webhook() — the /webhooks/stripe route that
Stripe calls when a fan's checkout session completes.

This had zero test coverage before this file. It's the only place that
flips a request from 'requested' to actually pinging the seller, and
Stripe explicitly documents that the same webhook event can be delivered
more than once (retries on a slow 2xx, duplicate sends, etc.). The route
guards against that by only acting "if req and req['status'] ==
'requested'" -- on any status other than 'requested' (e.g. already
'seller_pinged' from a first delivery) it must be a silent no-op: no
second seller ping, no overwritten stripe_payment_intent_id. That's the
same class of bug as the duplicate-insert issue documented in CLAUDE.md
section 4.3 (105,192 no-show rows from one game), just for this table
instead -- a second webhook delivery double-texting a seller would be a
real, visible production bug, so it's worth a direct regression test.

Drives the real Flask route via app.test_client() against a fake Stripe
client (controls what Webhook.construct_event returns/raises) and a fake
Supabase client, with ping_nfl_seller stubbed out so these tests cover
only stripe_webhook's own orchestration (verify -> look up -> guard ->
update + ping).
"""

import pytest

import fanxp_api


# ── Fake Supabase client (same shape as test_fanxp_api.py's) ────────────────

class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, table_name, store):
        self.table_name = table_name
        self.store = store
        self.filters = {}
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
        if self._maybe_single:
            # The documented postgrest quirk: zero rows -> None itself, not
            # a result object with `.data = None`.
            if not matching:
                return None
            return FakeResult(matching[0])
        return FakeResult(matching)


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


def make_request(store, **overrides):
    req = {
        "id": 5,
        "status": "requested",
        "section": "112",
        "row_label": "10",
        "seat_num": "5",
        "seller_id": 90,
        "stripe_payment_intent_id": None,
    }
    req.update(overrides)
    store.setdefault("nfl_seat_requests", []).append(req)
    return req


# ── Fake Stripe client ───────────────────────────────────────────────────────

class _FakeWebhook:
    def __init__(self, outcome):
        self._outcome = outcome
        self.calls = []

    def construct_event(self, payload, sig, secret):
        self.calls.append({"payload": payload, "sig": sig, "secret": secret})
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _FakeStripeClient:
    def __init__(self, outcome):
        self.Webhook = _FakeWebhook(outcome)


def stub_stripe(monkeypatch, outcome):
    client = _FakeStripeClient(outcome)
    monkeypatch.setattr(fanxp_api, "get_stripe", lambda: client)
    return client


def checkout_completed_event(request_id, payment_intent_id="pi_new123"):
    return {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "metadata": {"request_id": str(request_id)},
                "payment_intent": payment_intent_id,
            }
        },
    }


@pytest.fixture
def ping_spy(monkeypatch):
    calls = []
    monkeypatch.setattr(fanxp_api, "ping_nfl_seller", lambda sb, req: calls.append(req))
    return calls


@pytest.fixture
def client():
    return fanxp_api.app.test_client()


# ── Invalid signature ────────────────────────────────────────────────────────

def test_invalid_signature_returns_400_and_never_touches_supabase(client, monkeypatch, ping_spy):
    stub_stripe(monkeypatch, ValueError("bad signature"))

    def boom():
        raise AssertionError("get_supabase() must not be called when verification fails")
    monkeypatch.setattr(fanxp_api, "get_supabase", boom)

    resp = client.post("/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "bogus"})

    assert resp.status_code == 400
    assert "invalid webhook" in resp.get_json()["error"]
    assert ping_spy == []


# ── Event types other than checkout.session.completed are a no-op ──────────

def test_unrelated_event_type_is_a_no_op_and_never_touches_supabase(client, monkeypatch, ping_spy):
    stub_stripe(monkeypatch, {"type": "payment_intent.created", "data": {"object": {}}})

    def boom():
        raise AssertionError("get_supabase() must not be called for a non-checkout event")
    monkeypatch.setattr(fanxp_api, "get_supabase", boom)

    resp = client.post("/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "sig"})

    assert resp.status_code == 200
    assert resp.get_json() == {"received": True}
    assert ping_spy == []


# ── Unknown request_id in the session metadata is a silent no-op ───────────

def test_checkout_completed_for_unknown_request_id_is_a_no_op(client, monkeypatch, supabase, ping_spy):
    stub_stripe(monkeypatch, checkout_completed_event(request_id=999))
    # store has no row with id 999

    resp = client.post("/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "sig"})

    assert resp.status_code == 200
    assert resp.get_json() == {"received": True}
    assert ping_spy == []


# ── Happy path: first delivery for a 'requested' row ────────────────────────

def test_checkout_completed_for_a_requested_row_stores_intent_id_and_pings_seller(
    client, monkeypatch, supabase, ping_spy
):
    make_request(supabase, id=5, status="requested", stripe_payment_intent_id=None)
    stub_stripe(monkeypatch, checkout_completed_event(request_id=5, payment_intent_id="pi_abc123"))

    resp = client.post("/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "sig"})

    assert resp.status_code == 200
    assert resp.get_json() == {"received": True}

    updated = supabase["nfl_seat_requests"][0]
    assert updated["stripe_payment_intent_id"] == "pi_abc123"

    assert len(ping_spy) == 1
    assert ping_spy[0]["id"] == 5
    assert ping_spy[0]["stripe_payment_intent_id"] == "pi_abc123"


# ── Duplicate delivery of the same event: Stripe can and does redeliver ────

@pytest.mark.parametrize("already_status", ["seller_pinged", "confirmed", "declined", "expired"])
def test_duplicate_delivery_after_the_row_left_requested_does_not_re_ping(
    client, monkeypatch, supabase, ping_spy, already_status
):
    make_request(
        supabase,
        id=5,
        status=already_status,
        stripe_payment_intent_id="pi_original",
    )
    # Stripe redelivers the exact same event a second time.
    stub_stripe(monkeypatch, checkout_completed_event(request_id=5, payment_intent_id="pi_original"))

    resp = client.post("/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "sig"})

    assert resp.status_code == 200
    assert resp.get_json() == {"received": True}

    # No second ping, and the row is left exactly as it was.
    assert ping_spy == []
    updated = supabase["nfl_seat_requests"][0]
    assert updated["status"] == already_status
    assert updated["stripe_payment_intent_id"] == "pi_original"
