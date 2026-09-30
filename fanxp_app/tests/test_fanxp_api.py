"""
Tests for fanxp_api.py's NFL seat-request state machine: the seller
confirm/decline flow described in CLAUDE.md section 6 ("Payment flow").

This is the real, current product surface (backs docs/nfl_{slug}_seatmap.html)
and had zero test coverage before this file. It drives the pure request/
response logic (expiry, YES/NO resolution, SMS-reply parsing) against a fake
Supabase client and stubbed Stripe/Twilio helpers, so no real network access
or credentials are needed.

Two documented behaviors get direct regression coverage here:
  - The `.maybe_single()` postgrest quirk (`fetch_one()`'s whole reason for
    existing): a zero-row match returns None itself, not a response object
    with `.data = None`.
  - The try/except around Stripe capture in `resolve_nfl_seat_request()`:
    a failed capture must leave the request in `seller_pinged`, never
    silently mark a seat confirmed that was never paid for.
"""

from datetime import datetime, timedelta, timezone

import pytest

import fanxp_api
from fanxp_common import CREDIT_OFFER, NFL_OFFER_WINDOW_MINUTES


# ── Fake Supabase client ─────────────────────────────────────────────────────

class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, table_name, store):
        self.table_name = table_name
        self.store = store
        self.filters = {}
        self._in_filter = None
        self._limit = None
        self._order = None
        self._desc = False
        self._mode = "select"
        self._payload = None
        self._single = False
        self._maybe_single = False

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

    def in_(self, field, values):
        self._in_filter = (field, set(values))
        return self

    def order(self, field, desc=False):
        self._order = field
        self._desc = desc
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
        out = [r for r in rows if all(r.get(k) == v for k, v in self.filters.items())]
        if self._in_filter:
            field, values = self._in_filter
            out = [r for r in out if r.get(field) in values]
        if self._order:
            out = sorted(out, key=lambda r: r[self._order], reverse=self._desc)
        return out

    def execute(self):
        if self._mode == "insert":
            row = dict(self._payload)
            self.store.setdefault(self.table_name, []).append(row)
            return FakeResult([row])

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
def sb(store):
    return FakeSupabase(store)


@pytest.fixture
def fake_sms(monkeypatch):
    sent = []
    monkeypatch.setattr(fanxp_api, "send_or_log_sms", lambda twilio, to, body: sent.append((to, body)))
    monkeypatch.setattr(fanxp_api, "get_twilio", lambda: "fake-twilio-client")
    return sent


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
        "stripe_payment_intent_id": "pi_abc123",
    }
    req.update(overrides)
    store.setdefault("nfl_seat_requests", []).append(req)
    return req


def make_seller(store, **overrides):
    seller = {"id": 90, "name": "Jordan Smith", "phone": "+15559998888", "credit_balance": 0}
    seller.update(overrides)
    store.setdefault("nfl_sellers", []).append(seller)
    return seller


# ── fetch_one() ───────────────────────────────────────────────────────────

class _FakeExecuteResult:
    def __init__(self, data):
        self.data = data


class _FakeQueryWrapper:
    """Minimal stand-in exposing only .execute(), for testing fetch_one() in isolation."""
    def __init__(self, execute_return):
        self._execute_return = execute_return

    def execute(self):
        return self._execute_return


def test_fetch_one_returns_none_when_maybe_single_returns_none_itself():
    assert fanxp_api.fetch_one(_FakeQueryWrapper(None)) is None


def test_fetch_one_returns_data_on_a_match():
    row = {"id": 1, "status": "confirmed"}
    assert fanxp_api.fetch_one(_FakeQueryWrapper(_FakeExecuteResult(row))) == row


def test_fetch_one_returns_none_when_data_itself_is_none():
    assert fanxp_api.fetch_one(_FakeQueryWrapper(_FakeExecuteResult(None))) is None


# ── expire_if_stale() ─────────────────────────────────────────────────────

def test_terminal_status_is_returned_unchanged_without_touching_the_db(sb, store):
    req = {"status": "confirmed"}
    assert fanxp_api.expire_if_stale(sb, req) is req


def test_pinged_request_within_the_offer_window_is_left_alone(sb, store):
    req = make_request(
        store,
        status="seller_pinged",
        created_at=iso(datetime.now(timezone.utc) - timedelta(minutes=NFL_OFFER_WINDOW_MINUTES - 5)),
    )
    result = fanxp_api.expire_if_stale(sb, req)
    assert result["status"] == "seller_pinged"


def test_pinged_request_past_the_offer_window_expires_and_cancels_the_hold(sb, store, monkeypatch):
    cancelled = []
    fake_stripe = type("FakeStripe", (), {
        "PaymentIntent": type("PI", (), {"cancel": staticmethod(lambda pid: cancelled.append(pid))})
    })()
    monkeypatch.setattr(fanxp_api, "get_stripe", lambda: fake_stripe)

    req = make_request(
        store,
        status="seller_pinged",
        stripe_payment_intent_id="pi_expiring",
        created_at=iso(datetime.now(timezone.utc) - timedelta(minutes=NFL_OFFER_WINDOW_MINUTES + 1)),
    )
    result = fanxp_api.expire_if_stale(sb, req)

    assert result["status"] == "expired"
    assert cancelled == ["pi_expiring"]


def test_requested_status_past_the_window_also_expires(sb, store, monkeypatch):
    monkeypatch.setattr(fanxp_api, "get_stripe", lambda: (_ for _ in ()).throw(AssertionError("should not be called")))
    req = make_request(
        store,
        status="requested",
        stripe_payment_intent_id=None,
        created_at=iso(datetime.now(timezone.utc) - timedelta(minutes=NFL_OFFER_WINDOW_MINUTES + 30)),
    )
    result = fanxp_api.expire_if_stale(sb, req)
    assert result["status"] == "expired"


def test_expiry_swallows_a_stripe_cancel_failure_and_still_expires(sb, store, monkeypatch):
    def boom(_pid):
        raise RuntimeError("already captured on Stripe's side")
    fake_stripe = type("FakeStripe", (), {
        "PaymentIntent": type("PI", (), {"cancel": staticmethod(boom)})
    })()
    monkeypatch.setattr(fanxp_api, "get_stripe", lambda: fake_stripe)

    req = make_request(
        store,
        status="seller_pinged",
        created_at=iso(datetime.now(timezone.utc) - timedelta(minutes=NFL_OFFER_WINDOW_MINUTES + 1)),
    )
    result = fanxp_api.expire_if_stale(sb, req)
    assert result["status"] == "expired"


# ── resolve_nfl_seat_request() ───────────────────────────────────────────

def test_yes_with_no_payment_intent_confirms_without_calling_stripe(sb, store, fake_sms, monkeypatch):
    monkeypatch.setattr(fanxp_api, "get_stripe", lambda: (_ for _ in ()).throw(AssertionError("should not be called")))
    req = make_request(store, stripe_payment_intent_id=None)
    seller = make_seller(store)

    outcome = fanxp_api.resolve_nfl_seat_request(sb, req, seller, "YES")

    assert outcome == "confirmed"
    assert store["nfl_seat_requests"][0]["status"] == "confirmed"
    assert "pass_code" in store["nfl_seat_requests"][0]
    assert store["nfl_sellers"][0]["credit_balance"] == CREDIT_OFFER
    assert len(fake_sms) == 1
    assert fake_sms[0][0] == req["fan_phone"]


def test_yes_captures_payment_then_confirms_and_credits_seller(sb, store, fake_sms, monkeypatch):
    captured = []
    fake_stripe = type("FakeStripe", (), {
        "PaymentIntent": type("PI", (), {"capture": staticmethod(lambda pid: captured.append(pid))})
    })()
    monkeypatch.setattr(fanxp_api, "get_stripe", lambda: fake_stripe)

    req = make_request(store, stripe_payment_intent_id="pi_good")
    seller = make_seller(store, credit_balance=50)

    outcome = fanxp_api.resolve_nfl_seat_request(sb, req, seller, "YES")

    assert outcome == "confirmed"
    assert captured == ["pi_good"]
    assert store["nfl_sellers"][0]["credit_balance"] == 50 + CREDIT_OFFER


def test_yes_with_a_failed_capture_leaves_the_request_pinged_not_confirmed(sb, store, fake_sms, monkeypatch):
    """
    Regression test for the incident documented in CLAUDE.md section 6: a
    live rehearsal exposed an unhandled Stripe failure crashing the whole
    respond flow. The fix must never confirm a seat (or credit the seller,
    or text the fan) when the capture didn't actually go through.
    """
    def boom(_pid):
        raise RuntimeError("card declined on capture")
    fake_stripe = type("FakeStripe", (), {
        "PaymentIntent": type("PI", (), {"capture": staticmethod(boom)})
    })()
    monkeypatch.setattr(fanxp_api, "get_stripe", lambda: fake_stripe)

    req = make_request(store, status="seller_pinged", stripe_payment_intent_id="pi_bad")
    seller = make_seller(store, credit_balance=0)

    outcome = fanxp_api.resolve_nfl_seat_request(sb, req, seller, "YES")

    assert outcome == "capture_failed"
    assert store["nfl_seat_requests"][0]["status"] == "seller_pinged"
    assert "pass_code" not in store["nfl_seat_requests"][0]
    assert store["nfl_sellers"][0]["credit_balance"] == 0
    assert fake_sms == []


def test_no_cancels_the_hold_and_declines(sb, store, monkeypatch):
    cancelled = []
    fake_stripe = type("FakeStripe", (), {
        "PaymentIntent": type("PI", (), {"cancel": staticmethod(lambda pid: cancelled.append(pid))})
    })()
    monkeypatch.setattr(fanxp_api, "get_stripe", lambda: fake_stripe)

    req = make_request(store, stripe_payment_intent_id="pi_declined")
    seller = make_seller(store)

    outcome = fanxp_api.resolve_nfl_seat_request(sb, req, seller, "NO")

    assert outcome == "declined"
    assert cancelled == ["pi_declined"]
    assert store["nfl_seat_requests"][0]["status"] == "declined"


def test_no_without_a_payment_intent_still_declines_without_calling_stripe(sb, store, monkeypatch):
    monkeypatch.setattr(fanxp_api, "get_stripe", lambda: (_ for _ in ()).throw(AssertionError("should not be called")))
    req = make_request(store, stripe_payment_intent_id=None)
    seller = make_seller(store)

    outcome = fanxp_api.resolve_nfl_seat_request(sb, req, seller, "NO")

    assert outcome == "declined"
    assert store["nfl_seat_requests"][0]["status"] == "declined"


def test_no_swallows_a_stripe_cancel_failure_and_still_declines(sb, store, monkeypatch):
    def boom(_pid):
        raise RuntimeError("already cancelled on Stripe's side")
    fake_stripe = type("FakeStripe", (), {
        "PaymentIntent": type("PI", (), {"cancel": staticmethod(boom)})
    })()
    monkeypatch.setattr(fanxp_api, "get_stripe", lambda: fake_stripe)

    req = make_request(store, stripe_payment_intent_id="pi_x")
    seller = make_seller(store)

    outcome = fanxp_api.resolve_nfl_seat_request(sb, req, seller, "NO")
    assert outcome == "declined"
    assert store["nfl_seat_requests"][0]["status"] == "declined"


# ── apply_nfl_response() ──────────────────────────────────────────────────

def test_invalid_decision_short_circuits_before_touching_supabase(monkeypatch):
    monkeypatch.setattr(fanxp_api, "get_supabase", lambda: (_ for _ in ()).throw(AssertionError("should not be called")))
    result, error = fanxp_api.apply_nfl_response(123, "MAYBE")
    assert result is None
    assert error == "decision must be YES or NO"


def test_unknown_request_id_reports_not_found(sb, store, monkeypatch):
    monkeypatch.setattr(fanxp_api, "get_supabase", lambda: sb)
    result, error = fanxp_api.apply_nfl_response(999, "YES")
    assert result is None
    assert error == "not found"


def test_request_that_has_already_expired_is_reported_as_such(sb, store, monkeypatch):
    monkeypatch.setattr(fanxp_api, "get_supabase", lambda: sb)
    make_request(
        store,
        id=5,
        status="seller_pinged",
        stripe_payment_intent_id=None,
        created_at=iso(datetime.now(timezone.utc) - timedelta(minutes=NFL_OFFER_WINDOW_MINUTES + 5)),
    )
    result, error = fanxp_api.apply_nfl_response(5, "YES")
    assert result is None
    assert error == "request is 'expired', not awaiting a response"


def test_request_already_resolved_is_not_re_resolved(sb, store, monkeypatch):
    monkeypatch.setattr(fanxp_api, "get_supabase", lambda: sb)
    make_request(store, id=6, status="confirmed")
    result, error = fanxp_api.apply_nfl_response(6, "NO")
    assert result is None
    assert error == "request is 'confirmed', not awaiting a response"


def test_valid_pinged_request_is_resolved_and_returns_the_new_status(sb, store, fake_sms, monkeypatch):
    monkeypatch.setattr(fanxp_api, "get_supabase", lambda: sb)
    monkeypatch.setattr(fanxp_api, "get_stripe", lambda: (_ for _ in ()).throw(AssertionError("should not be called")))
    make_request(store, id=7, status="seller_pinged", stripe_payment_intent_id=None, seller_id=90)
    make_seller(store, id=90)

    result, error = fanxp_api.apply_nfl_response(7, "YES")

    assert error is None
    assert result == "confirmed"
    assert store["nfl_seat_requests"][0]["status"] == "confirmed"


# ── handle_nfl_sms_reply() ────────────────────────────────────────────────

@pytest.fixture
def resolve_spy(monkeypatch):
    calls = []
    monkeypatch.setattr(
        fanxp_api, "resolve_nfl_seat_request",
        lambda sb, req, seller, decision: calls.append((req["id"], decision)),
    )
    return calls


def test_unknown_phone_number_is_not_handled(sb, store, resolve_spy):
    handled = fanxp_api.handle_nfl_sms_reply(sb, "+15551234567", "YES")
    assert handled is False
    assert resolve_spy == []


def test_known_seller_phone_with_nothing_pending_is_swallowed(sb, store, resolve_spy):
    make_seller(store, id=1, phone="+15559998888")
    handled = fanxp_api.handle_nfl_sms_reply(sb, "+15559998888", "YES")
    assert handled is True
    assert resolve_spy == []


def test_yes_reply_resolves_the_pending_request(sb, store, resolve_spy):
    make_seller(store, id=1, phone="+15559998888")
    make_request(store, id=42, seller_id=1, status="seller_pinged")

    handled = fanxp_api.handle_nfl_sms_reply(sb, "+15559998888", "YES")

    assert handled is True
    assert resolve_spy == [(42, "YES")]


def test_no_reply_resolves_the_pending_request(sb, store, resolve_spy):
    make_seller(store, id=1, phone="+15559998888")
    make_request(store, id=42, seller_id=1, status="seller_pinged")

    handled = fanxp_api.handle_nfl_sms_reply(sb, "+15559998888", "NO")

    assert handled is True
    assert resolve_spy == [(42, "NO")]


def test_gibberish_reply_is_swallowed_without_resolving(sb, store, resolve_spy):
    make_seller(store, id=1, phone="+15559998888")
    make_request(store, id=42, seller_id=1, status="seller_pinged")

    handled = fanxp_api.handle_nfl_sms_reply(sb, "+15559998888", "MAYBE LATER")

    assert handled is True
    assert resolve_spy == []


def test_yes_followed_by_more_letters_does_not_match_word_boundary(sb, store, resolve_spy):
    """
    `^YES\\b` requires a word boundary right after "YES" — "YESSIR" has no
    boundary between the two S's, so it must not be treated as a YES.
    """
    make_seller(store, id=1, phone="+15559998888")
    make_request(store, id=42, seller_id=1, status="seller_pinged")

    handled = fanxp_api.handle_nfl_sms_reply(sb, "+15559998888", "YESSIR")

    assert handled is True
    assert resolve_spy == []


def test_explicit_request_id_in_the_reply_disambiguates_between_pending_requests(sb, store, resolve_spy):
    make_seller(store, id=1, phone="+15559998888")
    make_request(store, id=5, seller_id=1, status="seller_pinged")
    make_request(store, id=9, seller_id=1, status="seller_pinged")

    handled = fanxp_api.handle_nfl_sms_reply(sb, "+15559998888", "YES 5")

    assert handled is True
    assert resolve_spy == [(5, "YES")]


def test_no_id_in_the_reply_falls_back_to_the_most_recently_pinged_request(sb, store, resolve_spy):
    make_seller(store, id=1, phone="+15559998888")
    make_request(store, id=5, seller_id=1, status="seller_pinged")
    make_request(store, id=9, seller_id=1, status="seller_pinged")

    handled = fanxp_api.handle_nfl_sms_reply(sb, "+15559998888", "YES")

    assert handled is True
    assert resolve_spy == [(9, "YES")]
