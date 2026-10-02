"""
Tests for fanxp_api.answer_seat_question() -- the data-prep logic behind the
Telegram natural-language Q&A bot described in CLAUDE.md section 6.

This function had zero test coverage before this file. It is the real,
documented fallback chain the bot relies on:
  1. No games captured at all      -> a "no data yet" message, no OpenAI call.
  2. Most recent game has no_shows -> use them, basis = "confirmed empty".
  3. No no_shows yet, but pre_game
     listings exist for that game  -> fall back to listings, basis caveated
     as "not yet confirmed empty, just listed" (CLAUDE.md: "falls back to
     pre_game listings ... clearly caveated").
  4. Neither exists for that game  -> a "no seat data captured yet" message,
     no OpenAI call.

These matter because the whole point of the fallback (per CLAUDE.md) is that
the bot stays useful mid-game before the halftime scrape has run, WITHOUT
ever presenting un-confirmed pre-game listings as if they were confirmed
no-shows. A regression that dropped or mislabeled the caveat, or that pulled
the wrong game's data, would make the bot quietly lie to the one person
(the founder) who uses it.

Drives the function against a fake Supabase client and a fake OpenAI client
that records the prompt it was given, so no real network access, API key,
or credentials are needed.
"""

import pytest

import fanxp_api


# ── Fakes ────────────────────────────────────────────────────────────────────

class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, table_name, store):
        self.table_name = table_name
        self.store = store
        self.filters = {}
        self._limit = None
        self._order = None
        self._desc = False

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, field, value):
        self.filters[field] = value
        return self

    def order(self, field, desc=False):
        self._order = field
        self._desc = desc
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        rows = self.store.get(self.table_name, [])
        out = [r for r in rows if all(r.get(k) == v for k, v in self.filters.items())]
        if self._order:
            out = sorted(out, key=lambda r: r[self._order], reverse=self._desc)
        if self._limit is not None:
            out = out[: self._limit]
        return FakeResult(out)


class FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return FakeQuery(name, self.store)


class FakeOpenAI:
    """Records every chat.completions.create() call instead of hitting the
    real API, and returns a fixed canned answer."""

    def __init__(self, calls):
        self._calls = calls
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self._calls.append(kwargs)

        class _Msg:
            content = "canned answer"

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()


@pytest.fixture
def store():
    return {}


@pytest.fixture
def openai_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(fanxp_api, "get_openai", lambda: FakeOpenAI(calls))
    return calls


def _install_supabase(monkeypatch, store):
    monkeypatch.setattr(fanxp_api, "get_supabase", lambda: FakeSupabase(store))


def _game(game_id=1, opponent="Seattle Seahawks", game_date="2026-09-13"):
    return {
        "id": game_id,
        "league": "nfl",
        "home_team": "49ers",
        "opponent": opponent,
        "game_date": game_date,
    }


def _prompt_user_content(calls):
    assert len(calls) == 1
    messages = calls[0]["messages"]
    user_msg = next(m for m in messages if m["role"] == "user")
    return user_msg["content"]


def _prompt_system_content(calls):
    messages = calls[0]["messages"]
    return next(m for m in messages if m["role"] == "system")["content"]


# ── No game captured at all ─────────────────────────────────────────────────

def test_no_games_returns_message_without_calling_openai(monkeypatch, store, openai_calls):
    _install_supabase(monkeypatch, store)

    answer = fanxp_api.answer_seat_question("what's open?")

    assert answer == "I don't have any 49ers game data yet."
    assert openai_calls == []


# ── Neither no_shows nor pre_game listings exist for the latest game ───────

def test_no_data_for_latest_game_returns_message_without_calling_openai(monkeypatch, store, openai_calls):
    store["games"] = [_game(opponent="Arizona Cardinals", game_date="2026-09-20")]
    _install_supabase(monkeypatch, store)

    answer = fanxp_api.answer_seat_question("what's open?")

    assert answer == "No seat data captured yet for the Arizona Cardinals game (2026-09-20)."
    assert openai_calls == []


# ── no_shows exist -- confirmed-empty basis ────────────────────────────────

def test_no_shows_present_uses_confirmed_empty_basis(monkeypatch, store, openai_calls):
    store["games"] = [_game(game_id=1)]
    store["no_shows"] = [
        {"game_id": 1, "section": "112", "row": "10", "seat": "5", "price_usd": 150.0},
    ]
    store["listings"] = [
        # Pre-game listings also exist, but no_shows must win -- a seat that
        # sold between snapshots must never be presented as still empty.
        {"game_id": 1, "section": "999", "row": "1", "seat": "1", "price_usd": 999.0, "snapshot": "pre_game"},
    ]
    _install_supabase(monkeypatch, store)

    answer = fanxp_api.answer_seat_question("what's the best section?")

    assert answer == "canned answer"
    user_content = _prompt_user_content(openai_calls)
    assert "confirmed empty seats" in user_content
    assert "Sec 112, Row 10, Seat 5, $150" in user_content
    assert "999" not in user_content  # the stale pre_game-only listing must not leak in


def test_no_shows_with_missing_price_is_labeled_unknown_not_crashed(monkeypatch, store, openai_calls):
    store["games"] = [_game(game_id=1)]
    store["no_shows"] = [
        {"game_id": 1, "section": "112", "row": "10", "seat": "5", "price_usd": None},
    ]
    _install_supabase(monkeypatch, store)

    fanxp_api.answer_seat_question("what's open?")

    user_content = _prompt_user_content(openai_calls)
    assert "Sec 112, Row 10, Seat 5, price unknown" in user_content


# ── No no_shows yet, but pre_game listings exist -- caveated fallback ──────

def test_no_shows_absent_falls_back_to_pre_game_listings_with_caveat(monkeypatch, store, openai_calls):
    store["games"] = [_game(game_id=1, opponent="Denver Broncos", game_date="2026-09-27")]
    store["no_shows"] = []
    store["listings"] = [
        {"game_id": 1, "section": "201", "row": "3", "seat": "8", "price_usd": 85.0, "snapshot": "pre_game"},
    ]
    _install_supabase(monkeypatch, store)

    answer = fanxp_api.answer_seat_question("what's open?")

    assert answer == "canned answer"
    user_content = _prompt_user_content(openai_calls)
    assert "aren't confirmed empty, just listed" in user_content
    assert "Sec 201, Row 3, Seat 8, $85" in user_content
    assert "Denver Broncos" in user_content
    assert "2026-09-27" in user_content


def test_halftime_listings_are_not_mistaken_for_pre_game_fallback_data(monkeypatch, store, openai_calls):
    # Only halftime-snapshot listings exist (no pre_game, no no_shows yet) --
    # the fallback explicitly reads snapshot == "pre_game", so this must
    # still report no data rather than silently using the wrong snapshot.
    store["games"] = [_game(game_id=1, opponent="Arizona Cardinals", game_date="2026-09-20")]
    store["listings"] = [
        {"game_id": 1, "section": "201", "row": "3", "seat": "8", "price_usd": 85.0, "snapshot": "halftime"},
    ]
    _install_supabase(monkeypatch, store)

    answer = fanxp_api.answer_seat_question("what's open?")

    assert answer == "No seat data captured yet for the Arizona Cardinals game (2026-09-20)."
    assert openai_calls == []


# ── Only the most recent game's data is used ────────────────────────────────

def test_only_the_most_recent_games_no_shows_are_used(monkeypatch, store, openai_calls):
    store["games"] = [
        _game(game_id=1, opponent="Los Angeles Rams", game_date="2026-09-07"),
        _game(game_id=2, opponent="Seattle Seahawks", game_date="2026-09-14"),
    ]
    store["no_shows"] = [
        {"game_id": 1, "section": "OLD", "row": "1", "seat": "1", "price_usd": 10.0},
        {"game_id": 2, "section": "112", "row": "10", "seat": "5", "price_usd": 150.0},
    ]
    _install_supabase(monkeypatch, store)

    fanxp_api.answer_seat_question("what's open?")

    user_content = _prompt_user_content(openai_calls)
    assert "Seattle Seahawks" in user_content
    assert "Sec 112" in user_content
    assert "OLD" not in user_content


# ── Question text and reasoning_effort reach the model call ────────────────

def test_question_text_is_passed_through_to_the_model(monkeypatch, store, openai_calls):
    store["games"] = [_game(game_id=1)]
    store["no_shows"] = [{"game_id": 1, "section": "112", "row": "10", "seat": "5", "price_usd": 150.0}]
    _install_supabase(monkeypatch, store)

    fanxp_api.answer_seat_question("what's open in sections 110-115?")

    user_content = _prompt_user_content(openai_calls)
    assert "what's open in sections 110-115?" in user_content


def test_reasoning_effort_is_set_to_minimal(monkeypatch, store, openai_calls):
    # Regression guard for the documented gpt-5-nano gotcha (CLAUDE.md
    # section 6): without this, the model can spend its whole token budget
    # on internal reasoning and return empty text.
    store["games"] = [_game(game_id=1)]
    store["no_shows"] = [{"game_id": 1, "section": "112", "row": "10", "seat": "5", "price_usd": 150.0}]
    _install_supabase(monkeypatch, store)

    fanxp_api.answer_seat_question("what's open?")

    assert openai_calls[0]["reasoning_effort"] == "minimal"
    assert openai_calls[0]["model"] == "gpt-5-nano"
