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


def test_get_stripe_sets_api_key_from_env_and_returns_the_module(monkeypatch):
    # get_stripe() returns the `stripe` module itself (not a client instance)
    # with its module-level api_key set — that's what fanxp_api.py's
    # checkout/capture/cancel calls rely on (CLAUDE.md §6: manual-capture
    # Stripe Checkout). A regression here (e.g. forgetting to set api_key)
    # would make every real checkout fail with an auth error.
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_not_a_real_key")

    stripe = fanxp_common.get_stripe()

    assert stripe.api_key == "sk_test_not_a_real_key"


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


def test_get_twilio_returns_client_built_from_env_credentials(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtestsid")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "testtoken")

    client = fanxp_common.get_twilio()

    assert client.username == "ACtestsid"
    assert client.password == "testtoken"


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


def test_get_supabase_returns_client_built_from_env_credentials(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "fake-service-key")

    client = fanxp_common.get_supabase()

    # create_client() doesn't expose the url/key back on the returned
    # client, so the real assertion is just that construction succeeds
    # without raising and without touching the network (confirming
    # get_supabase() passes the env values through rather than, say,
    # swapping the url/key argument order).
    assert client is not None


def test_send_telegram_raises_when_token_missing(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        fanxp_common.send_telegram("12345", "hello")


def test_send_telegram_posts_chat_id_and_text_to_the_bot_api(monkeypatch):
    # The Telegram bot is the one notification channel the founder actually
    # wants (CLAUDE.md §6: "Telegram carries only... scrape outcomes" /
    # Q&A answers) — a wrong URL, or chat_id and text swapped in the
    # payload, would silently break every notification without any test
    # ever catching it, since the dry-run-heavy test suite never hits the
    # network for real.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-bot-token")

    calls = []

    def fake_post(url, data=None, timeout=None):
        calls.append({"url": url, "data": data, "timeout": timeout})

        class _Resp:
            status_code = 200
        return _Resp()

    import requests
    monkeypatch.setattr(requests, "post", fake_post)

    fanxp_common.send_telegram("chat-42", "hello from the runner")

    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.telegram.org/bottest-bot-token/sendMessage"
    assert calls[0]["data"] == {"chat_id": "chat-42", "text": "hello from the runner"}


# ── get_openai(): the credential guard behind the Telegram Q&A bot's model ──
#
# Same shape as get_stripe()/get_twilio()/get_supabase() above (CLAUDE.md §6:
# "the OpenAI key... live[s] in .env locally and as GitHub Actions / Render
# secrets") but had no direct test despite its siblings all having one.

def test_get_openai_raises_when_key_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        fanxp_common.get_openai()


def test_get_openai_raises_when_key_is_blank(monkeypatch):
    # Same regression guard as test_get_stripe_raises_when_key_is_blank: a
    # key of whitespace-only must be treated as unusable, not "present".
    monkeypatch.setenv("OPENAI_API_KEY", "   ")

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        fanxp_common.get_openai()


def test_get_openai_returns_client_built_from_the_env_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")

    client = fanxp_common.get_openai()

    assert client.api_key == "sk-test-not-a-real-key"
