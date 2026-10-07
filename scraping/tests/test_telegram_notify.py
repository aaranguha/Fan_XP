"""
Tests for telegram_notify.send_telegram() — the only Telegram sender wired
into the active NFL pipeline (nfl_run_game.py imports it directly for its
one-message-per-game scrape outcome alerts; CLAUDE.md §6 — "Telegram carries
only these scrape outcomes").

Its documented contract (own docstring) is: silently no-op if either
TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing, so a scrape run never
breaks over notification config being absent — and never let a network
failure while sending propagate, since that would crash the run it's meant
to be reporting on. Both guarantees are exercised here against a fake
`requests.post`, with no real network access or credentials.
"""

import pytest

import telegram_notify


@pytest.fixture(autouse=True)
def clear_telegram_env(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)


# ── Missing-config no-op guard ───────────────────────────────────────────────

def test_missing_both_env_vars_is_a_silent_noop(monkeypatch):
    calls = []
    monkeypatch.setattr(telegram_notify.requests, "post", lambda *a, **k: calls.append((a, k)))

    telegram_notify.send_telegram("hello")

    assert calls == []


def test_missing_bot_token_is_a_silent_noop(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    calls = []
    monkeypatch.setattr(telegram_notify.requests, "post", lambda *a, **k: calls.append((a, k)))

    telegram_notify.send_telegram("hello")

    assert calls == []


def test_missing_chat_id_is_a_silent_noop(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    calls = []
    monkeypatch.setattr(telegram_notify.requests, "post", lambda *a, **k: calls.append((a, k)))

    telegram_notify.send_telegram("hello")

    assert calls == []


def test_blank_env_vars_are_treated_as_missing(monkeypatch):
    # os.getenv(..., "") default means an explicitly-blank env var (not just
    # an unset one) must also trip the no-op guard, not get sent as a
    # literal empty token/chat_id.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "   ")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    calls = []
    monkeypatch.setattr(telegram_notify.requests, "post", lambda *a, **k: calls.append((a, k)))

    telegram_notify.send_telegram("hello")

    assert calls == []


# ── Actually sends when configured ───────────────────────────────────────────

def test_sends_with_token_and_chat_id_in_url_and_payload(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    calls = []
    monkeypatch.setattr(telegram_notify.requests, "post", lambda *a, **k: calls.append((a, k)))

    telegram_notify.send_telegram("✅ Packers vs Falcons: scraped.")

    assert len(calls) == 1
    (url,), kwargs = calls[0]
    assert url == "https://api.telegram.org/botfake-token/sendMessage"
    assert kwargs["data"] == {"chat_id": "12345", "text": "✅ Packers vs Falcons: scraped."}
    assert kwargs["timeout"] == 10


def test_token_and_chat_id_are_stripped_of_surrounding_whitespace(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "  fake-token  ")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "  12345  ")
    calls = []
    monkeypatch.setattr(telegram_notify.requests, "post", lambda *a, **k: calls.append((a, k)))

    telegram_notify.send_telegram("hello")

    (url,), kwargs = calls[0]
    assert url == "https://api.telegram.org/botfake-token/sendMessage"
    assert kwargs["data"]["chat_id"] == "12345"


# ── Network failures never propagate ─────────────────────────────────────────

def test_post_exception_is_swallowed_not_raised(monkeypatch, capsys):
    # A Telegram outage/timeout must never crash the scrape run it's
    # reporting on -- it should be logged and swallowed, not re-raised.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    def boom(*a, **k):
        raise ConnectionError("telegram is down")

    monkeypatch.setattr(telegram_notify.requests, "post", boom)

    telegram_notify.send_telegram("hello")  # must not raise

    assert "telegram is down" in capsys.readouterr().out
