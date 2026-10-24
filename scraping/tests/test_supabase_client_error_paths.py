"""
Tests for supabase_client.py's defensive error-handling paths that the
existing test_supabase_client.py doesn't reach: _get_client() itself (the
credential guard and client-caching every other function in the module
relies on, and which every other test in this suite bypasses by
monkeypatching `_get_client` directly instead of exercising it), plus the
try/except branches in upsert_game(), count_listings(),
fetch_no_shows_for_game(), and insert_no_shows() that swallow an unexpected
Supabase/network error and degrade to a safe default (None/0/[]) instead of
crashing the caller.

These functions are optional-write plumbing shared by all four leagues'
runners (CLAUDE.md: "all writes are optional -- if SUPABASE_URL /
SUPABASE_SERVICE_KEY are not set, calls are silently skipped so the local
CSV pipeline keeps working unchanged"). A bug in any of these except
branches -- e.g. one that referenced an undefined name in its own error
message -- would turn a transient Supabase hiccup into an unhandled crash of
whichever runner called it, which is exactly the failure mode this
module exists to prevent.

All driven against a fake `supabase` module / fake client -- no real network
access or credentials needed.
"""

import sys
import types

import pytest

import supabase_client as sc


@pytest.fixture(autouse=True)
def reset_client_cache(monkeypatch):
    # _get_client() caches its result in the module-level `_client` global.
    # Without resetting it, whichever test runs first to successfully build
    # a client would leak that client into every later test in this file.
    monkeypatch.setattr(sc, "_client", None)
    yield
    monkeypatch.setattr(sc, "_client", None)


# ── _get_client(): missing credentials ──────────────────────────────────────

def test_get_client_returns_none_when_url_missing(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "key")

    assert sc._get_client() is None


def test_get_client_returns_none_when_key_missing(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)

    assert sc._get_client() is None


def test_get_client_returns_none_when_both_missing(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)

    assert sc._get_client() is None


def test_get_client_treats_whitespace_only_values_as_missing(monkeypatch):
    # os.getenv(...).strip() -- a var set to blank/whitespace (e.g. an
    # accidentally-empty GitHub Actions secret) must be treated the same as
    # unset, not passed through to create_client() as an empty string.
    monkeypatch.setenv("SUPABASE_URL", "   ")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "   ")

    assert sc._get_client() is None


# ── _get_client(): successful construction, stripping, and caching ──────────

def _install_fake_supabase_module(monkeypatch, factory):
    fake_module = types.ModuleType("supabase")
    fake_module.create_client = factory
    monkeypatch.setitem(sys.modules, "supabase", fake_module)


def test_get_client_strips_whitespace_before_calling_create_client(monkeypatch):
    calls = []

    def fake_create_client(url, key):
        calls.append((url, key))
        return object()

    _install_fake_supabase_module(monkeypatch, fake_create_client)
    monkeypatch.setenv("SUPABASE_URL", "  https://proj.supabase.co  ")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "  secret-key  ")

    client = sc._get_client()

    assert client is not None
    assert calls == [("https://proj.supabase.co", "secret-key")]


def test_get_client_caches_and_does_not_rebuild_on_second_call(monkeypatch):
    calls = []

    def fake_create_client(url, key):
        calls.append((url, key))
        return object()

    _install_fake_supabase_module(monkeypatch, fake_create_client)
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "secret-key")

    first = sc._get_client()
    # Even if the env changed in between (e.g. a test or process mutated
    # os.environ), a cached client must be returned as-is, not rebuilt.
    monkeypatch.setenv("SUPABASE_URL", "https://different.supabase.co")
    second = sc._get_client()

    assert first is second
    assert len(calls) == 1


def test_get_client_returns_none_and_does_not_raise_when_create_client_fails(monkeypatch, capsys):
    def fake_create_client(url, key):
        raise RuntimeError("network unreachable")

    _install_fake_supabase_module(monkeypatch, fake_create_client)
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "secret-key")

    assert sc._get_client() is None
    assert "Failed to init client" in capsys.readouterr().out


def test_get_client_failed_init_is_not_cached_and_retries_next_call(monkeypatch):
    # A transient failure (network blip during a GitHub Actions run) must
    # not permanently poison the module -- a later call in the same process
    # should get a fresh attempt, not a cached None.
    attempts = {"n": 0}

    def flaky_create_client(url, key):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient failure")
        return "a-real-client"

    _install_fake_supabase_module(monkeypatch, flaky_create_client)
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "secret-key")

    assert sc._get_client() is None
    assert sc._get_client() == "a-real-client"
    assert attempts["n"] == 2


# ── Shared fake client for exercising the other functions' except branches ──

class RaisingQuery:
    """Every chain method returns self; execute() always raises, simulating
    an unexpected Supabase/network error after credentials were valid."""

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, *_a, **_k):
        return self

    def upsert(self, *_a, **_k):
        return self

    def insert(self, *_a, **_k):
        return self

    def execute(self):
        raise RuntimeError("simulated Supabase failure")


class RaisingClient:
    def table(self, _name):
        return RaisingQuery()


# ── upsert_game(): except branch ─────────────────────────────────────────────

def test_upsert_game_returns_none_and_does_not_raise_on_client_error(monkeypatch, capsys):
    monkeypatch.setattr(sc, "_get_client", lambda: RaisingClient())

    assert sc.upsert_game({"home_team": "magic", "game_date": "2026-04-01"}) is None
    assert "upsert_game failed" in capsys.readouterr().out


def test_upsert_game_returns_none_when_upsert_response_has_no_data(monkeypatch):
    # upsert_game only returns an id inside `if result.data:` -- an upsert
    # that "succeeds" but comes back with an empty data list must fall
    # through to the function's implicit None, not raise an IndexError.
    class EmptyDataQuery(RaisingQuery):
        def execute(self):
            class _Result:
                data = []
            return _Result()

    class EmptyDataClient:
        def table(self, _name):
            return EmptyDataQuery()

    monkeypatch.setattr(sc, "_get_client", lambda: EmptyDataClient())

    assert sc.upsert_game({"home_team": "magic", "game_date": "2026-04-01"}) is None


# ── count_listings(): except branch ──────────────────────────────────────────

def test_count_listings_returns_zero_and_does_not_raise_on_client_error(monkeypatch, capsys):
    monkeypatch.setattr(sc, "_get_client", lambda: RaisingClient())

    assert sc.count_listings(1, "pre_game") == 0
    assert "count_listings failed" in capsys.readouterr().out


# ── fetch_no_shows_for_game(): except branch ─────────────────────────────────

def test_fetch_no_shows_for_game_returns_empty_list_and_does_not_raise_on_client_error(monkeypatch, capsys):
    monkeypatch.setattr(sc, "_get_client", lambda: RaisingClient())

    assert sc.fetch_no_shows_for_game(1) == []
    assert "fetch_no_shows_for_game failed" in capsys.readouterr().out


# ── insert_no_shows(): except branch and its own price_usd parsing ──────────

def test_insert_no_shows_does_not_raise_when_insert_itself_fails(monkeypatch, capsys):
    monkeypatch.setattr(sc, "_get_client", lambda: RaisingClient())

    sc.insert_no_shows(
        7,
        [{"section": "116", "row": "1", "seat": "1", "price_usd": "42.50"}],
        "magic",
        "2026-04-01",
    )  # must not raise

    assert "insert_no_shows failed" in capsys.readouterr().out


@pytest.mark.parametrize("raw,expected", [
    ("42.50", 42.5),
    (42.5, 42.5),
    (None, None),
    ("", None),
    ("None", None),
    ("not-a-number", None),
])
def test_insert_no_shows_price_usd_parsing(monkeypatch, raw, expected):
    # insert_no_shows() duplicates insert_listings()'s price_usd parsing in
    # its own function body (separate try/except, not a shared helper), so a
    # fix or regression in one does not automatically cover the other.
    class RecordingQuery:
        def __init__(self):
            self.inserted = None

        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def insert(self, records):
            self.inserted = records
            return self

        def execute(self):
            class _Result:
                data = []
                count = 0
            return _Result()

    query_log = {}

    class RecordingClient:
        def table(self, name):
            q = query_log.setdefault(name, RecordingQuery())
            return q

    monkeypatch.setattr(sc, "_get_client", lambda: RecordingClient())

    sc.insert_no_shows(
        7,
        [{"section": "116", "row": "1", "seat": "1", "price_usd": raw}],
        "magic",
        "2026-04-01",
    )

    assert query_log["no_shows"].inserted[0]["price_usd"] == expected
