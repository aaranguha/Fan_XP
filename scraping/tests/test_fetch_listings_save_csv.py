"""
Tests for fetch_listings.py's save_csv().

CLAUDE.md §4.2 documents a real incident this function's behavior is central
to: "save_csv() legitimately writes nothing when a scrape returns zero rows
(confirmed live, 2026-09-09: the halftime scrape got back only 4 real seats
from TM ... and never even got that far because this used to crash on the
missing file first) -- a missing halftime.csv is a valid, expected state,
not an error." nfl_run_game.py's run_snapshot()/main() both rely on exactly
this: an empty scrape must NOT create the CSV file at all, so downstream
`os.path.isfile(csv_path)` checks correctly treat "file absent" as "no rows,"
and run_snapshot()'s own "already exists -> load instead of re-scraping"
short-circuit relies on the file only existing when there was real data to
write. None of this had test coverage.
"""

import csv
import os

import fetch_listings as fl


def _read_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── Empty input: the documented "no file at all" behavior ──────────────────

def test_empty_rows_does_not_create_the_file(tmp_path):
    out = tmp_path / "halftime.csv"
    fl.save_csv([], str(out))
    assert not out.exists()


def test_empty_rows_does_not_require_the_parent_directory_to_exist(tmp_path):
    # The real incident this guards against: a halftime scrape that finds
    # zero listings must not blow up just because nothing has created
    # gdir/ yet on this path -- the early return has to happen before any
    # directory or file operation.
    out = tmp_path / "does_not_exist_yet" / "halftime.csv"
    fl.save_csv([], str(out))
    assert not out.parent.exists()
    assert not out.exists()


def test_empty_rows_prints_no_rows_message(tmp_path, capsys):
    fl.save_csv([], str(tmp_path / "halftime.csv"))
    assert "No rows to save." in capsys.readouterr().out


# ── First write: creates parent dir, writes header + rows ──────────────────

def test_first_write_creates_missing_parent_directory(tmp_path):
    out = tmp_path / "team" / "game" / "pre_game.csv"
    fl.save_csv([{"section": "101", "row": "A", "price_usd": "50"}], str(out))
    assert out.is_file()


def test_first_write_includes_header_row(tmp_path):
    out = tmp_path / "pre_game.csv"
    fl.save_csv([{"section": "101", "row": "A", "price_usd": "50"}], str(out))
    header = out.read_text(encoding="utf-8").splitlines()[0]
    assert header == "section,row,price_usd"


def test_first_write_round_trips_row_values(tmp_path):
    out = tmp_path / "pre_game.csv"
    rows = [
        {"section": "101", "row": "A", "price_usd": "50"},
        {"section": "102", "row": "B", "price_usd": "75"},
    ]
    fl.save_csv(rows, str(out))
    assert _read_rows(str(out)) == rows


def test_column_order_follows_the_first_rows_key_order(tmp_path):
    out = tmp_path / "pre_game.csv"
    fl.save_csv([{"price_usd": "50", "section": "101"}], str(out))
    header = out.read_text(encoding="utf-8").splitlines()[0]
    assert header == "price_usd,section"


# ── Second write: append without re-writing the header ─────────────────────

def test_second_call_appends_rows_without_a_second_header(tmp_path):
    out = tmp_path / "pre_game.csv"
    fl.save_csv([{"section": "101", "row": "A"}], str(out))
    fl.save_csv([{"section": "102", "row": "B"}], str(out))

    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines == ["section,row", "101,A", "102,B"]


def test_second_call_preserves_rows_from_the_first_call(tmp_path):
    out = tmp_path / "pre_game.csv"
    fl.save_csv([{"section": "101", "row": "A"}], str(out))
    fl.save_csv([{"section": "102", "row": "B"}], str(out))

    rows = _read_rows(str(out))
    assert rows == [{"section": "101", "row": "A"}, {"section": "102", "row": "B"}]


def test_second_call_with_an_empty_list_leaves_existing_file_untouched(tmp_path):
    out = tmp_path / "pre_game.csv"
    fl.save_csv([{"section": "101", "row": "A"}], str(out))
    before = out.read_text(encoding="utf-8")

    fl.save_csv([], str(out))

    assert out.read_text(encoding="utf-8") == before
