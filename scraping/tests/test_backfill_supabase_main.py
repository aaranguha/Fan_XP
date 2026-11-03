"""
Tests for backfill_supabase.py's main() — the actual backfill driver loop,
as opposed to its pure filesystem helpers (load_csv/find_game_folders,
covered in test_backfill_supabase.py).

main() has real branching this one-time script depends on getting right if
it's ever re-run (CLAUDE.md's open item about MLB's no_shows table needing a
historical cleanup someday raises exactly that prospect for a sibling
pipeline):
  - a game folder with no game_date in its meta is skipped, not inserted
  - a failed upsert_game() (returns None) must skip the listings/no_shows
    inserts for that game too, not insert orphaned rows under a missing
    game_id
  - pre_game/halftime/no_shows CSVs are only inserted when they actually
    have rows — an empty CSV must not trigger a pointless insert call
  - halftime and pre_game rows are inserted as two separate calls tagged
    with their own snapshot label, never merged into one
  - --dry-run must never touch supabase_client at all, while still
    accumulating the same totals a real run would report

These drive main() against a fake supabase_client (monkeypatched onto the
backfill_supabase module) and a tmp_path DATA_ROOT, so no network/credentials
are needed.
"""

import json

import pytest

import backfill_supabase


def _make_game(data_root, team_slug, game_folder, game_date="2026-01-15",
                pre_rows=True, ht_rows=True, ns_rows=True, with_meta=True):
    game_path = data_root / team_slug / game_folder
    game_path.mkdir(parents=True)
    if with_meta:
        meta = {"game_date": game_date} if game_date is not None else {}
        (game_path / "game_meta.json").write_text(json.dumps(meta))
    if pre_rows:
        (game_path / "pre_game.csv").write_text("section,row,seat\n101,5,10\n102,3,2\n")
    if ht_rows:
        (game_path / "halftime.csv").write_text("section,row,seat\n101,5,10\n")
    if ns_rows:
        (game_path / "no_shows.csv").write_text("section,row,seat\n102,3,2\n")
    return game_path


class FakeSupabaseClient:
    def __init__(self, upsert_result=1):
        self.upsert_result = upsert_result
        self.upsert_calls = []
        self.insert_listings_calls = []
        self.insert_no_shows_calls = []

    def upsert_game(self, meta, league="nba"):
        self.upsert_calls.append((meta, league))
        return self.upsert_result

    def insert_listings(self, game_id, rows, snapshot, team_slug, game_date):
        self.insert_listings_calls.append((game_id, rows, snapshot, team_slug, game_date))

    def insert_no_shows(self, game_id, rows, team_slug, game_date):
        self.insert_no_shows_calls.append((game_id, rows, team_slug, game_date))


@pytest.fixture(autouse=True)
def restore_dry_run():
    original = backfill_supabase.DRY_RUN
    yield
    backfill_supabase.DRY_RUN = original


def test_dry_run_accumulates_totals_without_touching_supabase(tmp_path, monkeypatch, capsys):
    data_root = tmp_path / "data"
    _make_game(data_root, "warriors", "2026-01-15_lakers")

    monkeypatch.setattr(backfill_supabase, "DATA_ROOT", str(data_root))
    monkeypatch.setattr(backfill_supabase, "DRY_RUN", True)
    fake = FakeSupabaseClient()
    monkeypatch.setattr(backfill_supabase, "supabase_client", fake, raising=False)

    backfill_supabase.main()

    assert fake.upsert_calls == []
    assert fake.insert_listings_calls == []
    assert fake.insert_no_shows_calls == []

    out = capsys.readouterr().out
    assert "Games:     1" in out
    assert "Listings:  3" in out  # 2 pre_game rows + 1 halftime row
    assert "No-shows:  1" in out


def test_game_with_no_game_date_is_skipped(tmp_path, monkeypatch, capsys):
    data_root = tmp_path / "data"
    _make_game(data_root, "warriors", "2026-01-15_lakers", game_date=None)

    monkeypatch.setattr(backfill_supabase, "DATA_ROOT", str(data_root))
    monkeypatch.setattr(backfill_supabase, "DRY_RUN", False)
    fake = FakeSupabaseClient()
    monkeypatch.setattr(backfill_supabase, "supabase_client", fake, raising=False)

    backfill_supabase.main()

    assert fake.upsert_calls == []
    out = capsys.readouterr().out
    assert "no game_date in meta" in out
    assert "Skipped:   1" in out


def test_failed_upsert_skips_listings_and_no_shows_inserts(tmp_path, monkeypatch, capsys):
    data_root = tmp_path / "data"
    _make_game(data_root, "warriors", "2026-01-15_lakers")

    monkeypatch.setattr(backfill_supabase, "DATA_ROOT", str(data_root))
    monkeypatch.setattr(backfill_supabase, "DRY_RUN", False)
    fake = FakeSupabaseClient(upsert_result=None)
    monkeypatch.setattr(backfill_supabase, "supabase_client", fake, raising=False)

    backfill_supabase.main()

    assert len(fake.upsert_calls) == 1
    assert fake.insert_listings_calls == []
    assert fake.insert_no_shows_calls == []
    out = capsys.readouterr().out
    assert "upsert failed, skipping listings" in out
    assert "Skipped:   1" in out


def test_real_run_inserts_pre_game_and_halftime_as_separate_calls(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    _make_game(data_root, "warriors", "2026-01-15_lakers", game_date="2026-01-15")

    monkeypatch.setattr(backfill_supabase, "DATA_ROOT", str(data_root))
    monkeypatch.setattr(backfill_supabase, "DRY_RUN", False)
    fake = FakeSupabaseClient(upsert_result=42)
    monkeypatch.setattr(backfill_supabase, "supabase_client", fake, raising=False)

    backfill_supabase.main()

    assert fake.upsert_calls == [({"game_date": "2026-01-15"}, "nba")]
    assert len(fake.insert_listings_calls) == 2
    snapshots = {call[2] for call in fake.insert_listings_calls}
    assert snapshots == {"pre_game", "halftime"}
    for game_id, rows, snapshot, team_slug, game_date in fake.insert_listings_calls:
        assert game_id == 42
        assert team_slug == "warriors"
        assert game_date == "2026-01-15"
        if snapshot == "pre_game":
            assert len(rows) == 2
        else:
            assert len(rows) == 1

    assert len(fake.insert_no_shows_calls) == 1
    game_id, rows, team_slug, game_date = fake.insert_no_shows_calls[0]
    assert game_id == 42
    assert len(rows) == 1
    assert team_slug == "warriors"
    assert game_date == "2026-01-15"


def test_empty_csvs_do_not_trigger_insert_calls(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    _make_game(data_root, "warriors", "2026-01-15_lakers", pre_rows=False, ht_rows=False, ns_rows=False)

    monkeypatch.setattr(backfill_supabase, "DATA_ROOT", str(data_root))
    monkeypatch.setattr(backfill_supabase, "DRY_RUN", False)
    fake = FakeSupabaseClient(upsert_result=7)
    monkeypatch.setattr(backfill_supabase, "supabase_client", fake, raising=False)

    backfill_supabase.main()

    assert len(fake.upsert_calls) == 1
    assert fake.insert_listings_calls == []
    assert fake.insert_no_shows_calls == []


def test_totals_accumulate_correctly_across_multiple_games(tmp_path, monkeypatch, capsys):
    data_root = tmp_path / "data"
    _make_game(data_root, "warriors", "2026-01-15_lakers")
    _make_game(data_root, "warriors", "2026-01-20_nets", game_date=None)  # skipped

    monkeypatch.setattr(backfill_supabase, "DATA_ROOT", str(data_root))
    monkeypatch.setattr(backfill_supabase, "DRY_RUN", True)
    fake = FakeSupabaseClient()
    monkeypatch.setattr(backfill_supabase, "supabase_client", fake, raising=False)

    backfill_supabase.main()

    out = capsys.readouterr().out
    assert "Games:     1" in out
    assert "Skipped:   1" in out
