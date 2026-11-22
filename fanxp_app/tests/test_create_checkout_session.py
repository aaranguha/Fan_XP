"""
Tests for fanxp_api.create_checkout_session() — the helper that builds the
actual Stripe Checkout Session kwargs for a seat claim (CLAUDE.md section 6).

This had zero direct test coverage before this file. test_request_nfl_seat.py
exercises it indirectly (the route calls it and gets back a session), but
never inspects what was actually sent to Stripe — so the three deliberate,
inline-commented payment choices documented in fanxp_api.py were completely
unprotected:

  - `payment_intent_data={"capture_method": "manual"}` — the fan's card is
    authorized, not charged, until the seller confirms. Losing this would
    mean every claim charges the fan immediately with no seller-confirm step,
    which is the whole point of the product's fixed-price flow (CLAUDE.md
    section 2's "no auction" standing decision assumes this exact mechanic).
  - `payment_method_types=["card"]` — BNPL/wallet methods aren't tested
    against manual capture.
  - `managed_payments={"enabled": False}` — Stripe Tax is off for this
    marketplace-credit flow.

Also covered: the money passed through as `unit_amount` must reach Stripe
unchanged (this is real money), and `metadata.request_id` must round-trip as
the same value `stripe_webhook()` later does `int(session["metadata"]
["request_id"])` on — a wrong type there would crash the webhook, not just
look wrong in a dict.

Calls the function directly (no Flask route, no real Stripe) against a fake
`stripe.checkout.Session.create` that just records its kwargs.
"""

from types import SimpleNamespace

import fanxp_api


class FakeSession:
    @staticmethod
    def create(**kwargs):
        return SimpleNamespace(id="cs_test_123", url="https://checkout.stripe.com/pay/cs_test_123", _kwargs=kwargs)


class FakeCheckout:
    Session = FakeSession


class FakeStripe:
    checkout = FakeCheckout


def make_session(**overrides):
    kwargs = dict(
        stripe=FakeStripe,
        slug="49ers",
        section="112",
        row_label="10",
        seat_num="5",
        total_cents=12345,
        return_to_url="https://fan-xp.vercel.app/nfl_49ers_seatmap.html",
        request_id=42,
    )
    kwargs.update(overrides)
    return fanxp_api.create_checkout_session(**kwargs)


def test_payment_intent_uses_manual_capture():
    session = make_session()
    assert session._kwargs["payment_intent_data"] == {"capture_method": "manual"}


def test_payment_method_types_is_card_only():
    session = make_session()
    assert session._kwargs["payment_method_types"] == ["card"]


def test_managed_payments_disabled():
    session = make_session()
    assert session._kwargs["managed_payments"] == {"enabled": False}


def test_mode_is_payment():
    session = make_session()
    assert session._kwargs["mode"] == "payment"


def test_unit_amount_passes_total_cents_through_unchanged():
    session = make_session(total_cents=987654)
    [item] = session._kwargs["line_items"]
    assert item["price_data"]["unit_amount"] == 987654
    assert item["price_data"]["currency"] == "usd"
    assert item["quantity"] == 1


def test_unit_amount_is_not_mangled_for_zero_or_large_values():
    # $0 seats and very large totals shouldn't be coerced to float or
    # otherwise altered on the way to Stripe's unit_amount (cents, int).
    for cents in (0, 1, 999999999):
        session = make_session(total_cents=cents)
        [item] = session._kwargs["line_items"]
        assert item["price_data"]["unit_amount"] == cents
        assert isinstance(item["price_data"]["unit_amount"], int)


def test_product_name_includes_uppercased_slug_and_seat_identifiers():
    session = make_session(slug="patriots", section="C", row_label="7", seat_num="3")
    [item] = session._kwargs["line_items"]
    name = item["price_data"]["product_data"]["name"]
    assert "PATRIOTS" in name
    assert "Sec C" in name
    assert "Row 7" in name
    assert "Seat 3" in name


def test_success_and_cancel_urls_carry_request_id():
    session = make_session(
        return_to_url="https://fan-xp.vercel.app/nfl_49ers_seatmap.html",
        request_id=42,
    )
    assert session._kwargs["success_url"] == (
        "https://fan-xp.vercel.app/nfl_49ers_seatmap.html?request_id=42"
    )
    assert session._kwargs["cancel_url"] == (
        "https://fan-xp.vercel.app/nfl_49ers_seatmap.html?cancelled_request_id=42"
    )


def test_metadata_request_id_round_trips_as_the_int_the_webhook_expects():
    # stripe_webhook() later does int(session["metadata"]["request_id"]) to
    # look the request back up -- metadata values must be strings for
    # Stripe, but they must parse back to the exact same int.
    session = make_session(request_id=42)
    stored = session._kwargs["metadata"]["request_id"]
    assert isinstance(stored, str)
    assert int(stored) == 42


def test_function_returns_whatever_stripe_create_returns():
    session = make_session()
    assert session.id == "cs_test_123"
    assert session.url == "https://checkout.stripe.com/pay/cs_test_123"
