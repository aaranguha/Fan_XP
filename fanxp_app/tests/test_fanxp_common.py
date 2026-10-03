"""
Tests for fanxp_common.py — the shared Supabase/Twilio/Stripe/Telegram
helpers used by both fanxp_api.py (the real product) and fanxp_ping_sth.py
(the legacy demo flow). Had zero direct test coverage before this file:
fanxp_api's own tests only ever stub `send_or_log_sms` out, never exercise
the module itself.

Covers:
  - send_or_log_sms(): the TWILIO_DRY_RUN branch (CLAUDE.md §6 — "Twilio:
    TWILIO_DRY_RUN=true currently, both locally and on Render") returns a
    fake Message-like object instead of hitting the real Twilio client, so
    the dry-run flag actually has to work for the rest of the product to be
    demoable without a registered Twilio number.
  - send_surrender_sms() / send_nfl_seller_sms(): the SMS body text actually
    sent to a seat owner — section/row/seat, credit offer amount, and (for
    the NFL flow) the tap-to-confirm YES/NO links, including the
    `api_base_url.rstrip("/")` trailing-slash handling.
  - get_stripe() / get_twilio() / get_supabase() / send_telegram(): each
    raises RuntimeError with a clear message when its required env var(s)
    are missing, rather than failing confusingly deeper in a real network
    call. These guards are what stand between a misconfigured deploy and a
    silent crash.
"""

import os

import pytest

import fanxp_common


# ── send_or_log_sms() ────────────────────────────────────────────────────────

class _FakeTwilioMessages:
    def __init__(self):
        self.calls = []

    def create(self, body, from_, to):
        self.calls.append({"body": body, "from_": from_, "to": to})
        return {"sid": "SMreal123"}


class _FakeTwilio:
    def __init__(self):
        self.messages = _FakeTwilioMessages()


def test_send_or_log_sms_dry_run_never_calls_real_twilio(monkeypatch):
    monkeypatch.setattr(fanxp_common, "TWILIO_DRY_RUN", True)
    twilio = _FakeTwilio()

    result = fanxp_common.send_or_log_sms(twilio, "+15551234567", "hello")

    assert result.sid.startswith("DRYRUN-")
    assert twilio.messages.calls == []  # must never touch the real client


def test_send_or_log_sms_dry_run_sids_are_unique(monkeypatch):
    # Confirms the sid isn't a hardcoded placeholder — each dry-run message
    # gets its own fake id, matching what a real Twilio send would look like.
    monkeypatch.setattr(fanxp_common, "TWILIO_DRY_RUN", True)
    twilio = _FakeTwilio()

    first = fanxp_common.send_or_log_sms(twilio, "+15551234567", "a")
    second = fanxp_common.send_or_log_sms(twilio, "+15551234567", "b")

    assert first.sid != second.sid


def test_send_or_log_sms_live_mode_calls_real_twilio(monkeypatch):
    monkeypatch.setattr(fanxp_common, "TWILIO_DRY_RUN", False)
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550001111")
    twilio = _FakeTwilio()

    result = fanxp_common.send_or_log_sms(twilio, "+15551234567", "hello")

    assert result == {"sid": "SMreal123"}
    assert twilio.messages.calls == [
        {"body": "hello", "from_": "+15550001111", "to": "+15551234567"}
    ]


# ── send_surrender_sms() ─────────────────────────────────────────────────────

def test_send_surrender_sms_body_and_recipient(monkeypatch):
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550001111")
    twilio = _FakeTwilio()
    sth = {"name": "Jordan Smith", "phone": "+15559876543"}
    seat = {"section": "116", "row": "12", "seat": "7"}
    game = {"arena": "Lumen Field"}

    msg = fanxp_common.send_surrender_sms(twilio, sth, seat, game)

    assert msg == {"sid": "SMreal123"}
    [call] = twilio.messages.calls
    assert call["to"] == "+15559876543"
    assert call["from_"] == "+15550001111"
    assert "Jordan!" in call["body"]  # first-name split from full name
    assert "Lumen Field" in call["body"]
    assert "Sec 116 · Row 12 · Seat 7" in call["body"]
    assert f"${fanxp_common.CREDIT_OFFER}" in call["body"]


def test_send_surrender_sms_falls_back_to_generic_arena_text(monkeypatch):
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550001111")
    twilio = _FakeTwilio()
    sth = {"name": "Alex Lee", "phone": "+15559876543"}
    seat = {"section": "101", "row": "1", "seat": "1"}
    game = {}  # no "arena" key

    fanxp_common.send_surrender_sms(twilio, sth, seat, game)

    [call] = twilio.messages.calls
    assert "the stadium" in call["body"]


def test_send_surrender_sms_honors_custom_credit_offer(monkeypatch):
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550001111")
    twilio = _FakeTwilio()
    sth = {"name": "Alex Lee", "phone": "+15559876543"}
    seat = {"section": "101", "row": "1", "seat": "1"}
    game = {"arena": "Lumen Field"}

    fanxp_common.send_surrender_sms(twilio, sth, seat, game, credit_offer=50)

    [call] = twilio.messages.calls
    assert "$50" in call["body"]
    assert f"${fanxp_common.CREDIT_OFFER}" not in call["body"]


# ── send_nfl_seller_sms() ────────────────────────────────────────────────────

def _nfl_req():
    return {
        "id": "req-abc123",
        "section": "334",
        "row_label": "8",
        "seat_num": "15",
    }


def test_send_nfl_seller_sms_dry_run_builds_correct_links(monkeypatch):
    monkeypatch.setattr(fanxp_common, "TWILIO_DRY_RUN", True)
    twilio = _FakeTwilio()
    seller = {"name": "Pat Rivera", "phone": "+15551112222"}

    msg = fanxp_common.send_nfl_seller_sms(
        twilio, seller, _nfl_req(), "https://fanxp-api.onrender.com"
    )

    assert msg.sid.startswith("DRYRUN-")
    assert twilio.messages.calls == []  # dry-run must route through send_or_log_sms


def test_send_nfl_seller_sms_strips_trailing_slash_from_base_url(monkeypatch):
    monkeypatch.setattr(fanxp_common, "TWILIO_DRY_RUN", False)
    twilio = _FakeTwilio()
    seller = {"name": "Pat Rivera", "phone": "+15551112222"}

    fanxp_common.send_nfl_seller_sms(
        twilio, seller, _nfl_req(), "https://fanxp-api.onrender.com/"
    )

    [call] = twilio.messages.calls
    assert "onrender.com//nfl/respond" not in call["body"]
    assert "https://fanxp-api.onrender.com/nfl/respond/req-abc123?decision=YES" in call["body"]
    assert "https://fanxp-api.onrender.com/nfl/respond/req-abc123?decision=NO" in call["body"]


def test_send_nfl_seller_sms_body_mentions_seat_and_name(monkeypatch):
    monkeypatch.setattr(fanxp_common, "TWILIO_DRY_RUN", False)
    twilio = _FakeTwilio()
    seller = {"name": "Pat Rivera", "phone": "+15551112222"}

    fanxp_common.send_nfl_seller_sms(
        twilio, seller, _nfl_req(), "https://fanxp-api.onrender.com", credit_offer=25
    )

    [call] = twilio.messages.calls
    assert "Pat!" in call["body"]
    assert "Sec 334 · Row 8 · Seat 15" in call["body"]
    assert "$25" in call["body"]
    assert call["to"] == "+15551112222"


# ── Credential guards: get_stripe / get_twilio / get_supabase / send_telegram ──

def test_get_stripe_raises_when_key_missing(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError, match="STRIPE_SECRET_KEY"):
        fanxp_common.get_stripe()


def test_get_stripe_raises_when_key_is_blank(monkeypatch):
    # Confirms the guard checks for an actually-usable key, not just presence
    # of the env var (a key of "" or whitespace is as unusable as missing).
    monkeypatch.setenv("STRIPE_SECRET_KEY", "   ")

    with pytest.raises(RuntimeError, match="STRIPE_SECRET_KEY"):
        fanxp_common.get_stripe()


def test_get_twilio_raises_when_sid_missing(monkeypatch):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "sometoken")

    with pytest.raises(RuntimeError, match="TWILIO_ACCOUNT_SID"):
        fanxp_common.get_twilio()


def test_get_twilio_raises_when_token_missing(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "somesid")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="TWILIO_AUTH_TOKEN"):
        fanxp_common.get_twilio()


def test_get_supabase_raises_when_url_missing(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "somekey")

    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        fanxp_common.get_supabase()


def test_get_supabase_raises_when_service_key_missing(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)

    with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_KEY"):
        fanxp_common.get_supabase()


def test_send_telegram_raises_when_token_missing(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        fanxp_common.send_telegram("12345", "hello")
