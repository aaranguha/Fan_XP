"""
Tests for nightly_report.py's pure parsing/aggregation logic.

nightly_report.py runs after all game scrapers finish and writes a
plain-English diagnostic summary from whatever is on disk in data/ — it's
read by a human (or pasted to Claude) the morning after a scrape run, so
its row-counting and log-slicing logic needs to be right even on edge
cases like an empty/header-only CSV or a log with no entry for today.

These tests drive the module's helper functions directly against a
tmp_path-based data/ directory, monkeypatching DATA_DIR so nothing here
touches the real data/ folder or needs any network/credentials.
"""

from datetime import date

import pytest

import nightly_report as nr


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(nr, "DATA_DIR", str(tmp_path))
    return tmp_path


def _write_csv(path, data_rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write("col_a,col_b\n")
        for row in data_rows:
            f.write(row + "\n")


# ── check_game() / row_count() ──────────────────────────────────────────

def test_row_count_subtracts_header_from_normal_csv(tmp_path):
    team_dir = tmp_path / "49ers"
    folder = f"{date.today().isoformat()}_week1"
    game_path = team_dir / folder
    _write_csv(game_path / "pre_game.csv", ["a,1", "b,2", "c,3"])

    info = nr.check_game("49ers", folder, str(game_path))

    assert info["pre_rows"] == 3
    assert info["ht_rows"] is None  # halftime.csv doesn't exist
    assert info["ns_rows"] is None


def test_row_count_is_none_when_file_missing(tmp_path):
    game_path = tmp_path / "49ers" / "2026-10-05_week5"
    game_path.mkdir(parents=True)

    info = nr.check_game("49ers", "2026-10-05_week5", str(game_path))

    assert info["pre_rows"] is None
    assert info["ht_rows"] is None
    assert info["ns_rows"] is None


def test_row_count_for_header_only_csv_is_zero(tmp_path):
    # A scrape that ran but found literally zero listings still writes a
    # valid CSV with just the header row -- row_count must report 0, not
    # None (None means "no file at all", a different failure mode).
    game_path = tmp_path / "49ers" / "2026-10-05_week5"
    _write_csv(game_path / "no_shows.csv", [])

    info = nr.check_game("49ers", "2026-10-05_week5", str(game_path))

    assert info["ns_rows"] == 0


# ── get_game_folders() ──────────────────────────────────────────────────

def test_get_game_folders_only_matches_todays_date_prefix(tmp_path):
    today = date.today().isoformat()
    (tmp_path / "49ers" / f"{today}_week5").mkdir(parents=True)
    (tmp_path / "49ers" / "2020-01-01_week1").mkdir(parents=True)

    folders = nr.get_game_folders()

    teams_and_folders = [(t, f) for t, f, _ in folders]
    assert ("49ers", f"{today}_week5") in teams_and_folders
    assert ("49ers", "2020-01-01_week1") not in teams_and_folders


def test_get_game_folders_skips_non_directory_entries(tmp_path):
    today = date.today().isoformat()
    team_dir = tmp_path / "49ers"
    team_dir.mkdir(parents=True)
    (team_dir / "game.log").write_text("some log text")  # file, not a dir
    (team_dir / f"{today}_week5").mkdir()

    folders = nr.get_game_folders()

    assert [f for _, f, _ in folders] == [f"{today}_week5"]


def test_get_game_folders_ignores_files_at_data_dir_top_level(tmp_path):
    # DATA_DIR itself can contain loose files (e.g. nightly_report.txt from
    # a previous run) alongside per-team subdirectories -- these must be
    # skipped, not treated as a "team".
    today = date.today().isoformat()
    (tmp_path / "nightly_report.txt").write_text("previous report")
    (tmp_path / "49ers" / f"{today}_week5").mkdir(parents=True)

    folders = nr.get_game_folders()

    assert [t for t, _, _ in folders] == ["49ers"]


def test_get_game_folders_empty_when_no_teams_played_today(tmp_path):
    (tmp_path / "49ers" / "2020-01-01_week1").mkdir(parents=True)

    assert nr.get_game_folders() == []


# ── get_log_tail() ───────────────────────────────────────────────────────

def test_get_log_tail_returns_placeholder_when_log_missing(tmp_path):
    assert nr.get_log_tail("49ers") == "(no log)"


def test_get_log_tail_returns_section_after_last_run_header(tmp_path):
    team_dir = tmp_path / "49ers"
    team_dir.mkdir(parents=True)
    divider = "=" * 54
    log_text = (
        f"{divider}\nRun started 2026-10-04\nold run output, should be dropped\n"
        f"{divider}\nRun started 2026-10-05\nlatest run's real error output\n"
    )
    (team_dir / "game.log").write_text(log_text)

    tail = nr.get_log_tail("49ers", chars=800)

    assert "latest run's real error output" in tail
    assert "old run output, should be dropped" not in tail


def test_get_log_tail_truncates_to_requested_char_count(tmp_path):
    team_dir = tmp_path / "49ers"
    team_dir.mkdir(parents=True)
    (team_dir / "game.log").write_text("x" * 2000)

    tail = nr.get_log_tail("49ers", chars=50)

    assert len(tail) == 50


def test_get_log_tail_falls_back_to_whole_file_when_no_divider(tmp_path):
    team_dir = tmp_path / "49ers"
    team_dir.mkdir(parents=True)
    (team_dir / "game.log").write_text("plain log, no run-header dividers at all")

    tail = nr.get_log_tail("49ers", chars=800)

    assert tail == "plain log, no run-header dividers at all"


# ── get_daily_runner_summary() ───────────────────────────────────────────

def test_daily_runner_summary_missing_log_reports_not_found(tmp_path):
    assert nr.get_daily_runner_summary() == "(daily_runner.log not found)"


def test_daily_runner_summary_missing_todays_entry(tmp_path):
    (tmp_path / "daily_runner.log").write_text("[2020-01-01] old entry, unrelated\n")

    summary = nr.get_daily_runner_summary()

    assert "no entry for" in summary
    assert date.today().isoformat() in summary


def test_daily_runner_summary_returns_last_occurrence_of_todays_date(tmp_path):
    # daily_runner.py can log multiple blocks for today (e.g. a manual
    # restart after an earlier failed run) -- the *last* one is the
    # authoritative, most-recent summary, not the first.
    today = date.today().isoformat()
    log_text = (
        f"[{today}] first run this morning, now stale\n"
        f"[2026-09-01] unrelated older day\n"
        f"[{today}] second, most recent run -- this is the real summary\n"
    )
    (tmp_path / "daily_runner.log").write_text(log_text)

    summary = nr.get_daily_runner_summary()

    assert summary.startswith(f"[{today}] second, most recent run")
