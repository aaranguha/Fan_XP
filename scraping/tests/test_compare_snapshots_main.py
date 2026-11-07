"""
Tests for two still-uncovered parts of compare_snapshots.py:

  - print_report()'s "Sample no-show seats (first 10)" block: the seat-level
    vs. offer-level line format, and the truncation to the first 10 rows.
    test_compare_snapshots_io.py already covers print_report()'s sold-count
    and price-aggregation math, but never exercised this printed sample --
    coverage showed it as the only still-missing lines in that function.

  - main(): the CLI entry point GitHub Actions and the `nfl_run_game.py`
    family never call directly (they call compare()/save_no_shows() inline),
    but a human running `python compare_snapshots.py <team> <game_folder>`
    by hand does. It had zero test coverage: the argv-count usage/exit guard,
    and the full load -> compare -> save -> print pipeline wired to real
    files on disk via teams.get_team()'s slug lookup.
"""

import sys

import pytest

from compare_snapshots import main, print_report


# ── print_report(): "Sample no-show seats" block ────────────────────────────

def test_print_report_seat_level_sample_formats_sec_row_seat_and_price(capsys):
    no_shows = [
        {"section": "101", "row": "5", "seat": "10", "price_usd": "150", "selection_type": "resale"},
    ]
    pre_game = [{"section": "101", "row": "5", "seat": "10", "price_usd": "150"}]

    print_report(pre_game, pre_game, no_shows, "out.csv")

    out = capsys.readouterr().out
    assert "Sample no-show seats (first 10):" in out
    assert "Sec 101" in out
    assert "Row 5" in out
    assert "Seat 10" in out
    assert "resale" in out
    assert "$150" in out


def test_print_report_seat_level_sample_shows_na_when_price_missing(capsys):
    no_shows = [{"section": "101", "row": "5", "seat": "10"}]
    pre_game = [{"section": "101", "row": "5", "seat": "10"}]

    print_report(pre_game, pre_game, no_shows, "out.csv")

    assert "N/A" in capsys.readouterr().out


def test_print_report_offer_level_sample_uses_section_only_format(capsys):
    no_shows = [{"offer_id": "o1", "section": "Lower Bowl", "price_usd": "90", "selection_type": "single"}]
    pre_game = [{"offer_id": "o1", "section": "Lower Bowl", "price_usd": "90"}]

    print_report(pre_game, [], no_shows, "out.csv")

    out = capsys.readouterr().out
    assert "Section Lower Bowl" in out
    # Offer-level rows have no row/seat -- the seat-level "Sec/Row/Seat" format
    # must not be used for them.
    assert "Row" not in out
    assert "Seat " not in out


def test_print_report_sample_is_truncated_to_first_ten_no_shows(capsys):
    no_shows = [
        {"section": str(100 + i), "row": "1", "seat": str(i), "price_usd": "50"}
        for i in range(15)
    ]
    pre_game = [{"section": "1", "row": "1", "seat": "1", "price_usd": "50"}]

    print_report(pre_game, pre_game, no_shows, "out.csv")

    out = capsys.readouterr().out
    # Seats 0-9 are the first ten and must be printed; 10-14 must not be.
    for i in range(10):
        assert f"Seat {i} " in out
    for i in range(10, 15):
        assert f"Seat {i} " not in out


def test_print_report_skips_sample_block_entirely_when_no_no_shows(capsys):
    pre_game = [{"section": "101", "row": "5", "seat": "10", "price_usd": "150"}]

    print_report(pre_game, pre_game, [], "out.csv")

    assert "Sample no-show seats" not in capsys.readouterr().out


# ── main(): argv validation ──────────────────────────────────────────────────

def test_main_exits_1_and_prints_usage_with_too_few_args(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["compare_snapshots.py", "magic"])

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 1
    assert "Usage:" in capsys.readouterr().out


def test_main_exits_1_and_prints_usage_with_too_many_args(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["compare_snapshots.py", "magic", "game1", "extra"])

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 1
    assert "Usage:" in capsys.readouterr().out


def test_main_raises_a_clear_error_for_an_unknown_team_slug(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["compare_snapshots.py", "not_a_real_team", "game1"])

    with pytest.raises(ValueError, match="Unknown team"):
        main()


# ── main(): end-to-end driver ─────────────────────────────────────────────────

def _write_csv(path, rows):
    import csv
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def test_main_end_to_end_seat_level_writes_no_shows_file_and_prints_report(monkeypatch, tmp_path, capsys):
    gdir = tmp_path / "data" / "magic" / "2026-03-11_cavaliers_at_magic"
    _write_csv(gdir / "pre_game.csv", [
        {"section": "101", "row": "5", "seat": "10", "price_usd": "150"},
        {"section": "102", "row": "3", "seat": "4", "price_usd": "90"},
    ])
    _write_csv(gdir / "halftime.csv", [
        {"section": "101", "row": "5", "seat": "10", "price_usd": "150"},
    ])

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["compare_snapshots.py", "magic", "2026-03-11_cavaliers_at_magic"])

    main()

    out_path = gdir / "no_shows.csv"
    assert out_path.is_file()
    with open(out_path, newline="", encoding="utf-8") as f:
        import csv
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["seat"] == "10"

    report = capsys.readouterr().out
    assert "Confirmed no-shows:  1" in report
    assert "Full list saved to: data/magic/2026-03-11_cavaliers_at_magic/no_shows.csv" in report


def test_main_end_to_end_no_no_shows_does_not_create_output_file(monkeypatch, tmp_path):
    gdir = tmp_path / "data" / "magic" / "2026-03-11_cavaliers_at_magic"
    _write_csv(gdir / "pre_game.csv", [
        {"section": "101", "row": "5", "seat": "10", "price_usd": "150"},
    ])
    _write_csv(gdir / "halftime.csv", [
        {"section": "999", "row": "1", "seat": "1", "price_usd": "20"},
    ])

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["compare_snapshots.py", "magic", "2026-03-11_cavaliers_at_magic"])

    main()

    assert not (gdir / "no_shows.csv").exists()
