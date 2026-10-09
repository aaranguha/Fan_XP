"""
Tests for fanxp_api.get_or_create_nfl_seller() — looks up (or lazily creates)
the "seller" row representing whichever season ticket holder owns a given
section, for a given NFL team.

This had no direct test coverage: test_fanxp_api.py's fixtures insert
nfl_sellers rows straight into the fake store to set up scenarios for the
request-resolution state machine, but nothing exercises
get_or_create_nfl_seller() itself — its dedup-by-(team_slug, section) lookup,
its lazy-creation path, or its guard against a missing STH_PHONE env var
(the real phone number any newly-created seller gets texted at). A broken
dedup key here would double-text a season ticket holder's phone for every
new fan request on the same section, or (section-scoping bug) merge two
different teams' sellers into one record.

Drives the function against a minimal fake Supabase client, so no real
network access or credentials are needed.
"""

import pytest

import fanxp_api


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

    def eq(self, field, value):
        self.filters[field] = value
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        if self._mode == "insert":
            row = dict(self._payload)
            self.store.setdefault(self.table_name, []).append(row)
            return FakeResult([row])

        rows = self.store.get(self.table_name, [])
        matching = [r for r in rows if all(r.get(k) == v for k, v in self.filters.items())]
        limited = matching[: self._limit] if self._limit is not None else matching
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


def make_seller(store, **overrides):
    seller = {"id": 90, "team_slug": "49ers", "section": "112", "name": "Existing Seller", "phone": "+15559998888"}
    seller.update(overrides)
    store.setdefault("nfl_sellers", []).append(seller)
    return seller


# ── Reuse of an existing seller ───────────────────────────────────────────

def test_existing_seller_for_team_and_section_is_reused(sb, store, monkeypatch):
    monkeypatch.setenv("STH_PHONE", "+15550000000")
    existing = make_seller(store)

    result = fanxp_api.get_or_create_nfl_seller(sb, "49ers", "112")

    assert result == existing
    # No new row should have been inserted.
    assert len(store["nfl_sellers"]) == 1


def test_different_section_for_the_same_team_gets_its_own_seller(sb, store, monkeypatch):
    # Dedup must be scoped per-section, not just per-team, or two different
    # season ticket holders' sections would collapse into one seller/phone.
    monkeypatch.setenv("STH_PHONE", "+15550000000")
    make_seller(store, id=90, team_slug="49ers", section="112")

    result = fanxp_api.get_or_create_nfl_seller(sb, "49ers", "212")

    assert result["section"] == "212"
    assert result["team_slug"] == "49ers"
    assert len(store["nfl_sellers"]) == 2


def test_same_section_label_for_a_different_team_gets_its_own_seller(sb, store, monkeypatch):
    # Dedup must also be scoped per-team -- the same section number means
    # different seats (and a different real STH) for two different teams.
    monkeypatch.setenv("STH_PHONE", "+15550000000")
    make_seller(store, id=90, team_slug="49ers", section="112")

    result = fanxp_api.get_or_create_nfl_seller(sb, "seahawks", "112")

    assert result["team_slug"] == "seahawks"
    assert result["section"] == "112"
    assert len(store["nfl_sellers"]) == 2


# ── Lazy creation ──────────────────────────────────────────────────────────

def test_creates_a_new_seller_with_the_sth_phone_env_var(sb, store, monkeypatch):
    monkeypatch.setenv("STH_PHONE", "+15551234567")

    result = fanxp_api.get_or_create_nfl_seller(sb, "49ers", "112")

    assert result["team_slug"] == "49ers"
    assert result["section"] == "112"
    assert result["phone"] == "+15551234567"
    assert result["name"] == "Section 112 Season Ticket Holder"
    assert store["nfl_sellers"] == [result]


# ── Missing STH_PHONE guard ────────────────────────────────────────────────

def test_missing_sth_phone_env_var_raises(sb, store, monkeypatch):
    monkeypatch.delenv("STH_PHONE", raising=False)

    with pytest.raises(RuntimeError):
        fanxp_api.get_or_create_nfl_seller(sb, "49ers", "112")

    # Must not have inserted a half-created row before raising.
    assert store.get("nfl_sellers", []) == []


def test_whitespace_only_sth_phone_env_var_also_raises(sb, store, monkeypatch):
    # os.getenv(...).strip() -- a value of all whitespace must be treated
    # the same as unset, not passed through as a seller's phone number.
    monkeypatch.setenv("STH_PHONE", "   ")

    with pytest.raises(RuntimeError):
        fanxp_api.get_or_create_nfl_seller(sb, "49ers", "112")
