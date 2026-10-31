"""
Tests for backfill_meta.backfill() -- the end-to-end filesystem walk that
test_backfill_meta.py explicitly left out of scope ("backfill() itself ...
call[s] out to nba_api (a live network dependency) and [is] out of scope for
pure-logic unit tests"). This file closes that gap by faking the nba_api
import itself (via sys.modules injection, no real network/pandas dependency)
so the actual decision logic inside backfill() gets exercised directly:

  - Folders that already have a game_meta.json are skipped (counted, not
    overwritten).
  - Folders whose name doesn't match the "YYYY-MM-DD_opponent_at_team"
    shape (parse_folder() returns (None, None)) are silently skipped.
  - Non-directory entries at both the team level and the game level under
    data/ are ignored rather than raising.
  - When the nba_api import/lookup fails (the documented fail-closed path --
    this is also exactly what happens in a test environment that lacks
    nba_api, like test_run_game.py already documents), opponent_win_pct
    defaults to 0.5 and no opponent win/loss fields are written into meta.
  - When a standings lookup succeeds and finds the opponent by full name,
    the real won/loss record is merged into meta and feeds
    opponent_draw_score via team_draw_score(opp_slug, opp_win_pct).
  - The nickname-fallback match (an opponent not found by exact full name,
    matched instead by "standings key endswith nickname") still resolves to
    the right record -- the same category of name-matching fallback this
    repo has been bitten by before (CLAUDE.md's opponent-name parsing bug).
  - game_appeal_score is always (home_draw_score + opponent_draw_score) / 2,
    rounded the same way team_draw_score() itself rounds.
  - saved/skipped counters match what actually happened.

Faking nba_api: backfill() does `from nba_api.stats.endpoints import
leaguestandings` *inside* the function body, then calls
`leaguestandings.LeagueStandings().get_data_frames()[0]` and treats the
result as a pandas DataFrame (column assignment via `df["X"] = ...` and
`df.iterrows()`). FakeDataFrame below implements just enough of that
surface with plain Python lists/dicts -- no pandas needed -- so these tests
don't depend on whether nba_api/pandas happen to be installed in whatever
environment runs them.
"""

import json
import sys
import types

import pytest

import backfill_meta
from backfill_meta import backfill
from teams import team_draw_score


# ── Fake nba_api standings module (no pandas/network dependency) ───────────

class FakeColumn(list):
    def __add__(self, other):
        if isinstance(other, list):
            return FakeColumn(a + b for a, b in zip(self, other))
        return FakeColumn(a + other for a in self)


class FakeDataFrame:
    def __init__(self, rows):
        self._rows = [dict(r) for r in rows]

    def __getitem__(self, col):
        return FakeColumn(r[col] for r in self._rows)

    def __setitem__(self, col, values):
        for r, v in zip(self._rows, values):
            r[col] = v

    def iterrows(self):
        for i, r in enumerate(self._rows):
            yield i, dict(r)


def _install_fake_nba_api(monkeypatch, rows):
    """Injects a fake nba_api.stats.endpoints.leaguestandings module so
    `from nba_api.stats.endpoints import leaguestandings` inside backfill()
    resolves to it, regardless of whether the real nba_api package is
    installed."""

    class FakeLeagueStandings:
        def get_data_frames(self):
            return [FakeDataFrame(rows)]

    fake_mod = types.ModuleType("nba_api.stats.endpoints.leaguestandings")
    fake_mod.LeagueStandings = FakeLeagueStandings

    monkeypatch.setitem(sys.modules, "nba_api", types.ModuleType("nba_api"))
    monkeypatch.setitem(sys.modules, "nba_api.stats", types.ModuleType("nba_api.stats"))
    monkeypatch.setitem(sys.modules, "nba_api.stats.endpoints", types.ModuleType("nba_api.stats.endpoints"))
    monkeypatch.setitem(sys.modules, "nba_api.stats.endpoints.leaguestandings", fake_mod)


def _block_nba_api(monkeypatch):
    """Forces `import nba_api...` to raise ImportError, deterministically
    simulating the package not being installed (CPython treats a None
    entry in sys.modules as 'this import must fail')."""
    monkeypatch.setitem(sys.modules, "nba_api", None)


def _make_game_folder(data_dir, team_slug, folder_name):
    gdir = data_dir / team_slug / folder_name
    gdir.mkdir(parents=True)
    return gdir


def _read_meta(gdir):
    with open(gdir / "game_meta.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def chdir_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(backfill_meta, "DATA_DIR", "data")
    return tmp_path


# ── Skip conditions ──────────────────────────────────────────────────────

def test_skips_folder_that_already_has_game_meta(chdir_tmp, monkeypatch, capsys):
    _block_nba_api(monkeypatch)
    data_dir = chdir_tmp / "data"
    gdir = _make_game_folder(data_dir, "pacers", "2026-03-25_lakers_at_pacers")
    existing = {"home_team": "pacers", "note": "already here"}
    (gdir / "game_meta.json").write_text(json.dumps(existing))

    backfill()

    assert _read_meta(gdir) == existing  # untouched
    assert "0 created, 1 already existed" in capsys.readouterr().out


def test_skips_folder_that_does_not_match_game_folder_pattern(chdir_tmp, monkeypatch):
    _block_nba_api(monkeypatch)
    data_dir = chdir_tmp / "data"
    gdir = _make_game_folder(data_dir, "pacers", "not_a_game_folder")

    backfill()

    assert not (gdir / "game_meta.json").exists()


def test_skips_non_directory_entry_at_team_level(chdir_tmp, monkeypatch):
    _block_nba_api(monkeypatch)
    data_dir = chdir_tmp / "data"
    data_dir.mkdir()
    (data_dir / "README.txt").write_text("not a team")
    gdir = _make_game_folder(data_dir, "pacers", "2026-03-25_lakers_at_pacers")

    backfill()  # must not raise despite the stray file

    assert (gdir / "game_meta.json").exists()


def test_skips_non_directory_entry_at_game_level(chdir_tmp, monkeypatch):
    _block_nba_api(monkeypatch)
    data_dir = chdir_tmp / "data"
    team_dir = data_dir / "pacers"
    team_dir.mkdir(parents=True)
    (team_dir / "notes.txt").write_text("stray file, not a game folder")
    gdir = _make_game_folder(data_dir, "pacers", "2026-03-25_lakers_at_pacers")

    backfill()

    assert (gdir / "game_meta.json").exists()


# ── nba_api unavailable: fails closed, default win_pct ──────────────────────

def test_import_error_falls_back_to_default_win_pct_and_prints_warning(chdir_tmp, monkeypatch, capsys):
    _block_nba_api(monkeypatch)
    data_dir = chdir_tmp / "data"
    gdir = _make_game_folder(data_dir, "pacers", "2026-03-25_lakers_at_pacers")

    backfill()

    meta = _read_meta(gdir)
    assert "opponent_win_pct" not in meta  # record was {} -- nothing merged in
    assert meta["opponent"] == "Los Angeles Lakers"
    assert meta["home_team"] == "pacers"
    assert meta["game_date"] == "2026-03-25"
    assert meta["day_of_week"] == "Wednesday"
    assert meta["home_draw_score"] == team_draw_score("pacers")
    # opp_slug resolves ("lakers"), so opp_draw uses team_draw_score() with
    # the default win_pct=0.5 fallback, not the hardcoded fallback formula.
    assert meta["opponent_draw_score"] == team_draw_score("lakers", 0.5)
    assert meta["game_appeal_score"] == round(
        (team_draw_score("pacers") + team_draw_score("lakers", 0.5)) / 2, 2
    )
    out = capsys.readouterr().out
    assert "could not load NBA standings" in out
    assert "1 created, 0 already existed" in out


def test_import_error_uses_hardcoded_fallback_formula_for_unresolvable_opponent(chdir_tmp, monkeypatch):
    # An opponent slug that slug_from_fullname() can't resolve at all takes
    # the *other* fallback branch in backfill(): opp_draw is computed from
    # the raw win_pct via the hardcoded formula literal, not team_draw_score().
    _block_nba_api(monkeypatch)
    data_dir = chdir_tmp / "data"
    gdir = _make_game_folder(data_dir, "pacers", "2026-03-25_some_future_team_at_pacers")

    backfill()

    meta = _read_meta(gdir)
    assert meta["opponent"] == "Some Future Team"
    # opp_win_pct defaults to 0.5 (no standings), opp_slug is None, so:
    # opp_draw = round(0.5 * 10 * 0.2 + 5 * 0.8, 2) = round(1.0 + 4.0, 2) = 5.0
    assert meta["opponent_draw_score"] == 5.0
    assert meta["game_appeal_score"] == round((team_draw_score("pacers") + 5.0) / 2, 2)


# ── nba_api available: standings found and merged in ────────────────────────

def test_standings_match_by_full_name_merges_real_record(chdir_tmp, monkeypatch, capsys):
    _install_fake_nba_api(monkeypatch, [
        {"TeamCity": "Los Angeles", "TeamName": "Lakers", "WINS": 40, "LOSSES": 20},
    ])
    data_dir = chdir_tmp / "data"
    gdir = _make_game_folder(data_dir, "pacers", "2026-03-25_lakers_at_pacers")

    backfill()

    meta = _read_meta(gdir)
    assert meta["opponent_wins"] == 40
    assert meta["opponent_losses"] == 20
    assert meta["opponent_win_pct"] == round(40 / 60, 3)
    assert meta["opponent_draw_score"] == team_draw_score("lakers", 40 / 60)
    assert meta["game_appeal_score"] == round(
        (team_draw_score("pacers") + team_draw_score("lakers", 40 / 60)) / 2, 2
    )
    assert "Loaded standings for 1 teams." in capsys.readouterr().out


def test_standings_match_falls_back_to_nickname_when_full_name_key_absent(chdir_tmp, monkeypatch):
    # Standings cache is keyed by the literal "TeamCity TeamName" lowercase
    # string. If the opponent's full resolved name doesn't hit that key
    # directly (e.g. a city/market mismatch), backfill() falls back to
    # matching any cache key that *ends with* the opponent's last word.
    _install_fake_nba_api(monkeypatch, [
        {"TeamCity": "LA", "TeamName": "Lakers", "WINS": 10, "LOSSES": 30},
    ])
    data_dir = chdir_tmp / "data"
    # slug_to_opponent_name("lakers") -> "Los Angeles Lakers" (SLUG_TO_FULLNAME),
    # but the standings cache only has "la lakers" -- no exact match, so the
    # nickname fallback ("...endswith('lakers')") must still find it.
    gdir = _make_game_folder(data_dir, "pacers", "2026-03-25_lakers_at_pacers")

    backfill()

    meta = _read_meta(gdir)
    assert meta["opponent_wins"] == 10
    assert meta["opponent_losses"] == 30
    assert meta["opponent_win_pct"] == round(10 / 40, 3)


def test_unmatched_opponent_in_nonempty_standings_defaults_to_half(chdir_tmp, monkeypatch):
    # Standings loaded fine, but this particular opponent isn't in them at
    # all (neither exact nor nickname match) -- must still default cleanly
    # to 0.5 rather than raising a KeyError.
    _install_fake_nba_api(monkeypatch, [
        {"TeamCity": "Boston", "TeamName": "Celtics", "WINS": 50, "LOSSES": 10},
    ])
    data_dir = chdir_tmp / "data"
    gdir = _make_game_folder(data_dir, "pacers", "2026-03-25_lakers_at_pacers")

    backfill()

    meta = _read_meta(gdir)
    assert "opponent_wins" not in meta
    assert meta["opponent_draw_score"] == team_draw_score("lakers", 0.5)


# ── Static per-team fields ──────────────────────────────────────────────────

def test_known_team_gets_its_arena_and_city(chdir_tmp, monkeypatch):
    _block_nba_api(monkeypatch)
    data_dir = chdir_tmp / "data"
    gdir = _make_game_folder(data_dir, "pacers", "2026-03-25_lakers_at_pacers")

    backfill()

    meta = _read_meta(gdir)
    assert meta["arena"] == "Gainbridge Fieldhouse"
    assert meta["city"] == "Indianapolis"


def test_unknown_team_slug_gets_blank_arena_and_city(chdir_tmp, monkeypatch):
    _block_nba_api(monkeypatch)
    data_dir = chdir_tmp / "data"
    gdir = _make_game_folder(data_dir, "some_expansion_team", "2026-03-25_lakers_at_some_expansion_team")

    backfill()

    meta = _read_meta(gdir)
    assert meta["arena"] == ""
    assert meta["city"] == ""


# ── Multiple folders: saved/skipped counts add up correctly ────────────────

def test_saved_and_skipped_counts_across_multiple_folders(chdir_tmp, monkeypatch, capsys):
    _block_nba_api(monkeypatch)
    data_dir = chdir_tmp / "data"
    new_one = _make_game_folder(data_dir, "pacers", "2026-03-25_lakers_at_pacers")
    already_done = _make_game_folder(data_dir, "pacers", "2026-03-20_celtics_at_pacers")
    (already_done / "game_meta.json").write_text(json.dumps({"home_team": "pacers"}))
    not_a_game = _make_game_folder(data_dir, "pacers", "random_junk")

    backfill()

    assert (new_one / "game_meta.json").exists()
    assert not (not_a_game / "game_meta.json").exists()
    out = capsys.readouterr().out
    assert "1 created, 1 already existed" in out
