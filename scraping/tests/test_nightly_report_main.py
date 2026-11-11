"""
Tests for nightly_report.main()'s report-assembly logic: how it classifies
each game as succeeded/failed and what it writes to REPORT_OUT.

test_nightly_report.py already covers the module's lower-level helpers
(check_game()/row_count(), get_game_folders(), get_log_tail(),
get_daily_runner_summary()) in isolation. This file covers main() itself,
which wires those helpers together into the actual nightly diagnostic file
a human reads the morning after a scrape run.

The success/failure split matters for a documented, still-open real
incident (CLAUDE.md §9): several 2026-09 NFL games (Titans, Cardinals,
Vikings) had hundreds of halftime listings but 0 no-shows recorded -- a
likely seat-join mismatch in compare() that was never investigated because
nothing flagged it. main() classifies a game as "succeeded" only when
ns_rows is not None AND > 0, so a completed scrape that produced a
no_shows.csv with zero rows is deliberately reported as a FAILURE needing
investigation, not silently counted as a success. These tests pin that
behavior down as a regression guard.
"""

from datetime import date

import pytest

import nightly_report as nr


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(nr, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(nr, "REPORT_OUT", str(tmp_path / "nightly_report.txt"))
    return tmp_path


def _write_csv(path, data_rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write("col_a,col_b\n")
        for row in data_rows:
            f.write(row + "\n")


def _today_folder(team, tmp_path, suffix="week1"):
    today = date.today().isoformat()
    folder = f"{today}_{suffix}"
    return tmp_path / team / folder, folder


def test_no_games_found_reports_that_plainly(tmp_path, capsys):
    nr.main()

    report = (tmp_path / "nightly_report.txt").read_text()
    today = date.today().isoformat()

    assert f"No game folders found for {today}." in report
    assert "RESULTS:" not in report


def test_report_file_is_written_and_also_printed(tmp_path, capsys):
    nr.main()

    written = (tmp_path / "nightly_report.txt").read_text()
    printed = capsys.readouterr().out

    assert written.strip() in printed
    assert "FAN XP — NIGHTLY SCRAPE REPORT" in written


def test_game_with_positive_no_shows_counts_as_a_success(tmp_path):
    game_path, folder = _today_folder("49ers", tmp_path)
    _write_csv(game_path / "pre_game.csv", ["a,1"] * 10)
    _write_csv(game_path / "halftime.csv", ["a,1"] * 4)
    _write_csv(game_path / "no_shows.csv", ["a,1"] * 4)

    nr.main()

    report = (tmp_path / "nightly_report.txt").read_text()

    assert "RESULTS: 1 succeeded, 0 failed" in report
    assert "SUCCEEDED:" in report
    assert "[49ers] pre=10 | halftime=4 | no_shows=4" in report
    assert "FAILED" not in report


def test_game_with_zero_no_shows_counts_as_a_failure_not_a_success(tmp_path):
    # Regression guard for the documented Titans/Cardinals/Vikings incident
    # (CLAUDE.md §9): a no_shows.csv that exists but has zero data rows
    # must be flagged for investigation, not treated as a clean success.
    game_path, folder = _today_folder("titans", tmp_path)
    _write_csv(game_path / "pre_game.csv", ["a,1"] * 814)
    _write_csv(game_path / "halftime.csv", ["a,1"] * 700)
    _write_csv(game_path / "no_shows.csv", [])  # header only -- 0 rows

    nr.main()

    report = (tmp_path / "nightly_report.txt").read_text()

    assert "RESULTS: 0 succeeded, 1 failed" in report
    assert "SUCCEEDED:" not in report
    assert "FAILED — ERROR DETAILS:" in report
    assert f"[titans] {folder}" in report
    assert "pre=814 | halftime=700 | no_shows=0" in report


def test_game_missing_no_shows_csv_entirely_counts_as_a_failure(tmp_path):
    game_path, folder = _today_folder("broncos", tmp_path)
    _write_csv(game_path / "pre_game.csv", ["a,1"] * 3)
    # no halftime.csv, no no_shows.csv at all -- scrape never completed

    nr.main()

    report = (tmp_path / "nightly_report.txt").read_text()

    assert "RESULTS: 0 succeeded, 1 failed" in report
    assert f"[broncos] {folder}" in report
    assert "pre=3 | halftime=None | no_shows=None" in report


def test_failure_section_includes_that_teams_log_tail(tmp_path):
    game_path, folder = _today_folder("broncos", tmp_path)
    _write_csv(game_path / "pre_game.csv", [])
    log_dir = tmp_path / "broncos"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "game.log").write_text(
        "=" * 54 + "\nRun started\nTimeoutError: Page.goto exceeded 30000ms\n"
    )

    nr.main()

    report = (tmp_path / "nightly_report.txt").read_text()

    assert "TimeoutError: Page.goto exceeded 30000ms" in report


def test_mixed_successes_and_failures_both_sections_present_with_right_counts(tmp_path):
    ok_path, _ = _today_folder("49ers", tmp_path)
    _write_csv(ok_path / "pre_game.csv", ["a,1"] * 5)
    _write_csv(ok_path / "no_shows.csv", ["a,1"] * 2)

    bad_path, bad_folder = _today_folder("vikings", tmp_path)
    _write_csv(bad_path / "pre_game.csv", ["a,1"] * 659)
    _write_csv(bad_path / "no_shows.csv", [])

    nr.main()

    report = (tmp_path / "nightly_report.txt").read_text()

    assert "RESULTS: 1 succeeded, 1 failed" in report
    assert "[49ers] pre=5 | halftime=None | no_shows=2" in report
    assert f"[vikings] {bad_folder}" in report
    assert "pre=659 | halftime=None | no_shows=0" in report
