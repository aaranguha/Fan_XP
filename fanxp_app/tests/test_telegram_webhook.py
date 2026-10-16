"""
Tests for fanxp_api.telegram_webhook() -- the /webhooks/telegram route that
Telegram calls whenever a message arrives for the Fan XP bot (see CLAUDE.md
section 6, "Natural-language Q&A about empty seats").

This had zero test coverage before this file, despite three separate pieces
of real security/business logic living in one small route:

  1. Secret-token verification: when TELEGRAM_WEBHOOK_SECRET is set, a
     request missing or mismatching the X-Telegram-Bot-Api-Secret-Token
     header must be rejected with 403 *before* anything else runs (no
     answer_seat_question call, no send_telegram call) -- this is the only
     thing stopping an arbitrary POST to this public URL from triggering an
     OpenAI call or a Telegram send.
  2. Owner-only replies: only the configured TELEGRAM_CHAT_ID gets an answer;
     a message from any other chat is silently accepted (200 ok) but never
     answered or sent anywhere, since the bot is single-tenant (owner only).
  3. answer_seat_question() failures are caught and turned into a friendly
     message sent back via Telegram instead of a bare 500 -- the same
     "never crash a webhook on a downstream failure" pattern already
     regression-tested for Stripe (test_stripe_webhook.py) and the NFL
     respond flow, now covering the OpenAI-backed Q&A path too.

Drives the real Flask route via app.test_client(), with answer_seat_question
and send_telegram stubbed out so these tests cover only telegram_webhook's
own orchestration (verify secret -> check chat id -> answer -> relay).
"""

import pytest

import fanxp_api


CHAT_ID = "999888777"


def update_for(chat_id, text):
    return {"message": {"chat": {"id": chat_id}, "text": text}}


@pytest.fixture
def client():
    return fanxp_api.app.test_client()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT_ID)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)


@pytest.fixture
def answer_spy(monkeypatch):
    calls = []

    def fake_answer(question):
        calls.append(question)
        return "here's what's open"

    monkeypatch.setattr(fanxp_api, "answer_seat_question", fake_answer)
    return calls


@pytest.fixture
def telegram_spy(monkeypatch):
    calls = []
    monkeypatch.setattr(
        fanxp_api, "send_telegram", lambda chat_id, text: calls.append((chat_id, text))
    )
    return calls


def post(client, body, headers=None):
    return client.post("/webhooks/telegram", json=body, headers=headers or {})


# ── Secret-token verification ────────────────────────────────────────────────

def test_wrong_secret_is_rejected_before_answering_or_sending(
    client, monkeypatch, answer_spy, telegram_spy
):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT_ID)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "expected-secret")

    resp = post(
        client,
        update_for(CHAT_ID, "what's open?"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
    )

    assert resp.status_code == 403
    assert resp.get_json() == {"ok": False}
    assert answer_spy == []
    assert telegram_spy == []


def test_missing_secret_header_is_rejected_when_one_is_configured(
    client, monkeypatch, answer_spy, telegram_spy
):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT_ID)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "expected-secret")

    resp = post(client, update_for(CHAT_ID, "what's open?"))

    assert resp.status_code == 403
    assert answer_spy == []
    assert telegram_spy == []


def test_correct_secret_is_accepted(client, monkeypatch, answer_spy, telegram_spy):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT_ID)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "expected-secret")

    resp = post(
        client,
        update_for(CHAT_ID, "what's open?"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "expected-secret"},
    )

    assert resp.status_code == 200
    assert answer_spy == ["what's open?"]
    assert telegram_spy == [(CHAT_ID, "here's what's open")]


def test_no_secret_configured_skips_verification(client, env, answer_spy, telegram_spy):
    # TELEGRAM_WEBHOOK_SECRET unset (see `env` fixture) -- no header required.
    resp = post(client, update_for(CHAT_ID, "what's open?"))

    assert resp.status_code == 200
    assert answer_spy == ["what's open?"]


# ── Owner-only replies ───────────────────────────────────────────────────────

def test_message_from_a_different_chat_is_ignored(client, env, answer_spy, telegram_spy):
    resp = post(client, update_for("someone-elses-chat-id", "what's open?"))

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    assert answer_spy == []
    assert telegram_spy == []


def test_empty_text_is_ignored(client, env, answer_spy, telegram_spy):
    resp = post(client, update_for(CHAT_ID, ""))

    assert resp.status_code == 200
    assert answer_spy == []
    assert telegram_spy == []


def test_missing_message_text_is_ignored(client, env, answer_spy, telegram_spy):
    resp = post(client, {"message": {"chat": {"id": CHAT_ID}}})

    assert resp.status_code == 200
    assert answer_spy == []
    assert telegram_spy == []


def test_no_allowed_chat_id_configured_ignores_everyone(
    client, monkeypatch, answer_spy, telegram_spy
):
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)

    resp = post(client, update_for(CHAT_ID, "what's open?"))

    assert resp.status_code == 200
    assert answer_spy == []
    assert telegram_spy == []


def test_matching_chat_id_gets_answered_and_relayed(client, env, answer_spy, telegram_spy):
    resp = post(client, update_for(CHAT_ID, "what are the best sections?"))

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    assert answer_spy == ["what are the best sections?"]
    assert telegram_spy == [(CHAT_ID, "here's what's open")]


def test_text_is_stripped_of_surrounding_whitespace_before_answering(
    client, env, answer_spy, telegram_spy
):
    post(client, update_for(CHAT_ID, "  what's open?  \n"))

    assert answer_spy == ["what's open?"]


def test_chat_id_matching_is_exact_string_comparison(client, monkeypatch, answer_spy, telegram_spy):
    # TELEGRAM_CHAT_ID is read as a string env var; a Telegram chat id that's
    # numerically equal but spelled differently (e.g. a leading zero some
    # client tacked on) must not match -- no implicit numeric coercion.
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "007")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)

    resp = post(client, update_for(7, "what's open?"))

    assert resp.status_code == 200
    assert answer_spy == []


# ── answer_seat_question() failures are caught, not crashed on ─────────────

def test_answer_seat_question_exception_is_caught_and_relayed_as_an_error_message(
    client, monkeypatch, env, telegram_spy
):
    def boom(question):
        raise RuntimeError("OpenAI request failed")

    monkeypatch.setattr(fanxp_api, "answer_seat_question", boom)

    resp = post(client, update_for(CHAT_ID, "what's open?"))

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    assert len(telegram_spy) == 1
    sent_chat_id, sent_text = telegram_spy[0]
    assert sent_chat_id == CHAT_ID
    assert "something went wrong" in sent_text
    assert "OpenAI request failed" in sent_text


def test_send_telegram_itself_is_not_called_twice_on_success(client, env, answer_spy, telegram_spy):
    post(client, update_for(CHAT_ID, "what's open?"))

    assert len(telegram_spy) == 1
