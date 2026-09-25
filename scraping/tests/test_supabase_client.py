"""
Tests for supabase_client.py's insert/count helpers — the write layer shared
by all four leagues' runners.

Covers the duplicate-insert dedup guard in insert_listings()/insert_no_shows(),
which is the fix for a real production bug (CLAUDE.md §4.3): before this
guard existed, calling either function twice for the same game/snapshot (an
overlapping cron trigger, a manual re-run reloading its own already-scraped
CSV) inserted every row a second time with no error. One real Diamondbacks
MLB game ended up with 105,192 "no-show" rows — physically impossible for any
stadium. Both functions now check the existing row count first and no-op if
the game/snapshot already has data.

Also covers: the None/empty/"None"-string price_usd parsing (bad price
strings must not raise, they must fall back to a null price), the 500-row
insert batching, count_listings()/count_no_shows() short-circuiting to 0 when
Supabase isn't configured, and fetch_no_shows_for_game()'s 1000-row-page
pagination loop. All driven against a fake Supabase client — no real network
access or credentials needed.
"""

import pytest

import supabase_client as sc


class FakeResult:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count


class FakeQuery:
    def __init__(self, table_name, client):
        self.table_name = table_name
        self.client = client
        self.filters = {}
        self._count_mode = None
        self._select_fields = None
        self._range = None
        self._insert_records = None
        self._upsert_record = None
        self._upsert_on_conflict = None

    def select(self, *fields, count=None):
        self._select_fields = fields
        self._count_mode = count
        return self

    def eq(self, field, value):
        self.filters[field] = value
        return self

    def order(self, _field):
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def insert(self, records):
        self._insert_records = records
        self.client.insert_call_count += 1
        return self

    def upsert(self, record, on_conflict=None):
        self._upsert_record = record
        self._upsert_on_conflict = on_conflict
        return self

    def _matching(self):
        rows = self.client.store.get(self.table_name, [])
        return [r for r in rows if all(r.get(k) == v for k, v in self.filters.items())]

    def execute(self):
        self.client.calls.append((self.table_name, dict(self.filters)))

        if self._insert_records is not None:
            self.client.inserted.setdefault(self.table_name, []).extend(self._insert_records)
            if self.client.raise_on_insert:
                raise RuntimeError("simulated insert failure")
            return FakeResult(data=self._insert_records)

        if self._upsert_record is not None:
            row = dict(self._upsert_record)
            row["id"] = self.client.next_game_id
            self.client.upserted.append(row)
            return FakeResult(data=[row])

        matching = self._matching()
        if self._range is not None:
            start, end = self._range
            matching = matching[start:end + 1]

        count = len(self._matching()) if self._count_mode == "exact" else None
        return FakeResult(data=matching, count=count)


class FakeClient:
    def __init__(self, store=None, raise_on_insert=False, next_game_id=1):
        self.store = store or {}
        self.calls = []
        self.inserted = {}
        self.upserted = []
        self.raise_on_insert = raise_on_insert
        self.next_game_id = next_game_id
        self.insert_call_count = 0

    def table(self, name):
        return FakeQuery(name, self)


def _rows(n, price="42.50"):
    return [
        {"section": "116", "row": str(i), "seat": "1", "price_usd": price, "scraped_at": "t"}
        for i in range(n)
    ]


# ── insert_listings(): the duplicate-insert dedup guard ─────────────────────

def test_insert_listings_skips_when_snapshot_already_has_rows(monkeypatch, capsys):
    existing = [{"id": i, "game_id": 1, "snapshot": "pre_game"} for i in range(50)]
    client = FakeClient(store={"listings": existing})
    monkeypatch.setattr(sc, "_get_client", lambda: client)

    sc.insert_listings(1, _rows(10), "pre_game", "diamondbacks", "2026-09-01", league="mlb")

    assert client.inserted == {}
    assert "already exist" in capsys.readouterr().out


def test_insert_listings_inserts_when_snapshot_has_no_existing_rows(monkeypatch):
    client = FakeClient(store={"listings": []})
    monkeypatch.setattr(sc, "_get_client", lambda: client)

    sc.insert_listings(1, _rows(10), "pre_game", "diamondbacks", "2026-09-01", league="mlb")

    assert len(client.inserted["listings"]) == 10


def test_insert_listings_second_call_for_same_snapshot_is_a_noop(monkeypatch):
    # Regression test for the exact production incident: call insert_listings
    # twice for the same game_id/snapshot (as an overlapping cron trigger or a
    # re-run would) and confirm the second call inserts nothing extra.
    store = {"listings": []}
    client = FakeClient(store=store)
    monkeypatch.setattr(sc, "_get_client", lambda: client)

    sc.insert_listings(1, _rows(10), "pre_game", "diamondbacks", "2026-09-01", league="mlb")
    store["listings"] = client.inserted["listings"]  # simulate the insert landing in the store
    sc.insert_listings(1, _rows(10), "pre_game", "diamondbacks", "2026-09-01", league="mlb")

    assert len(client.inserted["listings"]) == 10


def test_insert_listings_noop_when_client_not_configured(monkeypatch):
    monkeypatch.setattr(sc, "_get_client", lambda: None)
    # Must not raise even though there is no client to call .table() on.
    sc.insert_listings(1, _rows(5), "pre_game", "team", "2026-09-01")


def test_insert_listings_noop_when_rows_empty(monkeypatch):
    client = FakeClient(store={"listings": []})
    monkeypatch.setattr(sc, "_get_client", lambda: client)

    sc.insert_listings(1, [], "pre_game", "team", "2026-09-01")

    assert client.calls == []


def test_insert_listings_noop_when_game_id_none(monkeypatch):
    client = FakeClient(store={"listings": []})
    monkeypatch.setattr(sc, "_get_client", lambda: client)

    sc.insert_listings(None, _rows(5), "pre_game", "team", "2026-09-01")

    assert client.calls == []


def test_insert_listings_batches_over_500_rows(monkeypatch):
    client = FakeClient(store={"listings": []})
    monkeypatch.setattr(sc, "_get_client", lambda: client)

    sc.insert_listings(1, _rows(1200), "pre_game", "team", "2026-09-01")

    assert client.insert_call_count == 3  # 500 + 500 + 200
    assert len(client.inserted["listings"]) == 1200


def test_insert_listings_swallows_exceptions(monkeypatch, capsys):
    client = FakeClient(store={"listings": []}, raise_on_insert=True)
    monkeypatch.setattr(sc, "_get_client", lambda: client)

    sc.insert_listings(1, _rows(5), "pre_game", "team", "2026-09-01")  # must not raise

    assert "insert_listings failed" in capsys.readouterr().out


# ── price_usd parsing (shared by insert_listings/insert_no_shows) ───────────

@pytest.mark.parametrize("raw,expected", [
    ("42.50", 42.5),
    (42.5, 42.5),
    (None, None),
    ("", None),
    ("None", None),
    ("not-a-number", None),
])
def test_price_usd_parsing(monkeypatch, raw, expected):
    client = FakeClient(store={"listings": []})
    monkeypatch.setattr(sc, "_get_client", lambda: client)

    sc.insert_listings(1, [{"section": "1", "row": "1", "seat": "1", "price_usd": raw}],
                        "pre_game", "team", "2026-09-01")

    assert client.inserted["listings"][0]["price_usd"] == expected


# ── insert_no_shows(): same dedup guard, separate table/counter ─────────────

def test_insert_no_shows_skips_when_game_already_has_no_shows(monkeypatch, capsys):
    existing = [{"id": i, "game_id": 7} for i in range(105192)]
    client = FakeClient(store={"no_shows": existing})
    monkeypatch.setattr(sc, "_get_client", lambda: client)

    sc.insert_no_shows(7, _rows(20), "diamondbacks", "2026-09-01", league="mlb")

    assert client.inserted == {}
    assert "already exist" in capsys.readouterr().out


def test_insert_no_shows_inserts_when_none_exist(monkeypatch):
    client = FakeClient(store={"no_shows": []})
    monkeypatch.setattr(sc, "_get_client", lambda: client)

    sc.insert_no_shows(7, _rows(20), "diamondbacks", "2026-09-01", league="mlb")

    assert len(client.inserted["no_shows"]) == 20


def test_insert_no_shows_noop_when_rows_empty(monkeypatch):
    client = FakeClient(store={"no_shows": []})
    monkeypatch.setattr(sc, "_get_client", lambda: client)

    sc.insert_no_shows(7, [], "team", "2026-09-01")

    assert client.calls == []


# ── count_listings() / count_no_shows() ──────────────────────────────────────

def test_count_listings_zero_when_client_not_configured(monkeypatch):
    monkeypatch.setattr(sc, "_get_client", lambda: None)

    assert sc.count_listings(1, "pre_game") == 0


def test_count_listings_zero_when_game_id_none(monkeypatch):
    client = FakeClient(store={"listings": [{"id": 1, "game_id": 1, "snapshot": "pre_game"}]})
    monkeypatch.setattr(sc, "_get_client", lambda: client)

    assert sc.count_listings(None, "pre_game") == 0


def test_count_listings_counts_only_matching_snapshot(monkeypatch):
    listings = (
        [{"id": i, "game_id": 1, "snapshot": "pre_game"} for i in range(30)]
        + [{"id": 100 + i, "game_id": 1, "snapshot": "halftime"} for i in range(5)]
    )
    client = FakeClient(store={"listings": listings})
    monkeypatch.setattr(sc, "_get_client", lambda: client)

    assert sc.count_listings(1, "pre_game") == 30
    assert sc.count_listings(1, "halftime") == 5


def test_count_no_shows_zero_when_client_not_configured(monkeypatch):
    monkeypatch.setattr(sc, "_get_client", lambda: None)

    assert sc.count_no_shows(1) == 0


# ── upsert_game() ────────────────────────────────────────────────────────────

def test_upsert_game_returns_none_when_client_not_configured(monkeypatch):
    monkeypatch.setattr(sc, "_get_client", lambda: None)

    assert sc.upsert_game({"home_team": "49ers"}, league="nfl") is None


def test_upsert_game_returns_new_id_on_success(monkeypatch):
    client = FakeClient(next_game_id=42)
    monkeypatch.setattr(sc, "_get_client", lambda: client)

    game_id = sc.upsert_game({"home_team": "49ers", "opponent": "rams", "game_date": "2026-09-13"}, league="nfl")

    assert game_id == 42
    assert client.upserted[0]["home_team"] == "49ers"
    assert client.upserted[0]["league"] == "nfl"


# ── fetch_no_shows_for_game(): pagination ────────────────────────────────────

def test_fetch_no_shows_for_game_pages_through_more_than_1000_rows(monkeypatch):
    rows = [{"id": i, "game_id": 1, "section": "1", "row": "1", "seat": str(i),
             "price_usd": 10, "selection_type": "resale"} for i in range(1500)]
    client = FakeClient(store={"no_shows": rows})
    monkeypatch.setattr(sc, "_get_client", lambda: client)

    result = sc.fetch_no_shows_for_game(1)

    assert len(result) == 1500


def test_fetch_no_shows_for_game_empty_when_client_not_configured(monkeypatch):
    monkeypatch.setattr(sc, "_get_client", lambda: None)

    assert sc.fetch_no_shows_for_game(1) == []
