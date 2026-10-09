"""
Tests for compare_snapshots.py's I/O and reporting helpers: load_csv(),
save_no_shows(), and print_report().

compare() itself (the no-show detection logic) already has thorough coverage
in test_compare_snapshots.py. These three functions were untested: they are
what main() actually calls before/after compare(), and print_report() in
particular carries real computation (sold-count for both schemas, price
aggregation for the dashboard's face-value/avg-price numbers) rather than
being pure boilerplate.

Writing the price-aggregation tests surfaced a real, live bug: print_report()
computes

    prices = [float(r["price_usd"]) for r in no_shows if r.get("price_usd")]

with no try/except. A malformed-but-truthy price_usd value (e.g. "N/A", a
placeholder from an incomplete scrape) raises an unhandled ValueError and
crashes the report instead of being skipped -- the exact same class of bug
already filed for generate_wnba_story.analyse_games() (issue #29) and
generate_nba_dashboard.fmt_name()/generate_mlb_story.fmt_name() (issues #33,
#28). Not fixed here per the QA-agent policy; see the companion GitHub issue.
"""

import csv

import pytest

from compare_snapshots import load_csv, save_no_shows, print_report


# ── load_csv() ───────────────────────────────────────────────────────────────

def test_load_csv_reads_rows_as_list_of_dicts(tmp_path):
    path = tmp_path / "pre_game.csv"
    path.write_text("section,row,seat,price_usd\n101,5,10,150\n102,3,4,90\n")

    rows = load_csv(str(path))

    assert rows == [
        {"section": "101", "row": "5", "seat": "10", "price_usd": "150"},
        {"section": "102", "row": "3", "seat": "4", "price_usd": "90"},
    ]


def test_load_csv_raises_filenotfound_for_missing_file(tmp_path):
    missing = tmp_path / "does_not_exist.csv"

    with pytest.raises(FileNotFoundError):
        load_csv(str(missing))


# ── save_no_shows() ──────────────────────────────────────────────────────────

def test_save_no_shows_writes_header_and_rows(tmp_path):
    out = tmp_path / "no_shows.csv"
    rows = [
        {"section": "101", "row": "5", "seat": "10", "price_usd": "150"},
        {"section": "101", "row": "6", "seat": "1", "price_usd": "90"},
    ]

    save_no_shows(rows, str(out))

    with open(out, newline="", encoding="utf-8") as f:
        written = list(csv.DictReader(f))
    assert written == rows


def test_save_no_shows_does_not_create_file_when_rows_empty(tmp_path):
    out = tmp_path / "no_shows.csv"

    save_no_shows([], str(out))

    assert not out.exists()


# ── print_report(): sold-count computation ───────────────────────────────────

def test_print_report_seat_level_sold_is_pregame_minus_noshows(capsys):
    pre = [
        {"section": "101", "row": "1", "seat": "1", "price_usd": "100"},
        {"section": "101", "row": "1", "seat": "2", "price_usd": "100"},
        {"section": "101", "row": "1", "seat": "3", "price_usd": "100"},
    ]
    no_shows = [pre[0]]

    print_report(pre, [pre[0]], no_shows, "out.csv")

    out = capsys.readouterr().out
    assert "Sold between scans:  2" in out
    assert "Confirmed no-shows:  1" in out


def test_print_report_offer_level_sold_dedupes_by_offer_id(capsys):
    # Two pre-game rows share the same offer_id (e.g. a multi-seat listing
    # recorded as one offer) -- sold count must be based on distinct
    # offer_ids, not raw row count.
    pre = [
        {"offer_id": "abc", "section": "Lower Bowl", "price_usd": "80"},
        {"offer_id": "abc", "section": "Lower Bowl", "price_usd": "80"},
        {"offer_id": "xyz", "section": "Upper Bowl", "price_usd": "40"},
    ]
    no_shows = [pre[2]]

    print_report(pre, [], no_shows, "out.csv")

    out = capsys.readouterr().out
    # 2 distinct offer_ids total, 1 confirmed no-show -> 1 sold.
    assert "Sold between scans:  1" in out


# ── print_report(): price aggregation ────────────────────────────────────────

def test_print_report_computes_total_and_avg_face_value(capsys):
    no_shows = [
        {"section": "101", "row": "1", "seat": "1", "price_usd": "100"},
        {"section": "101", "row": "1", "seat": "2", "price_usd": "50"},
    ]

    print_report(no_shows, no_shows, no_shows, "out.csv")

    out = capsys.readouterr().out
    assert "Total face value:   $150" in out
    assert "Avg no-show price:  $75" in out


def test_print_report_skips_price_block_when_no_no_shows(capsys):
    pre = [{"section": "101", "row": "1", "seat": "1", "price_usd": "100"}]

    print_report(pre, pre, [], "out.csv")

    out = capsys.readouterr().out
    assert "Confirmed no-shows:  0" in out
    assert "Total face value" not in out


def test_print_report_ignores_rows_with_missing_price_usd(capsys):
    no_shows = [
        {"section": "101", "row": "1", "seat": "1", "price_usd": "100"},
        {"section": "101", "row": "1", "seat": "2", "price_usd": ""},
    ]

    print_report(no_shows, no_shows, no_shows, "out.csv")

    out = capsys.readouterr().out
    # Only the row with a real price_usd contributes.
    assert "Total face value:   $100" in out
    assert "Avg no-show price:  $100" in out


@pytest.mark.xfail(
    reason=(
        "Real, live bug: print_report()'s price aggregation line -- "
        "'prices = [float(r[\"price_usd\"]) for r in no_shows if "
        "r.get(\"price_usd\")]' -- has no try/except around float(). A "
        "truthy-but-non-numeric price_usd value (e.g. 'N/A', a placeholder "
        "left by an incomplete scrape) raises an unhandled ValueError and "
        "crashes the whole no-show report instead of being skipped like a "
        "missing/empty price_usd is. Same bug class as issues #29/#33/#28. "
        "Not fixed here per the QA-agent policy; see the companion GitHub "
        "issue."
    ),
    strict=True,
)
def test_print_report_skips_malformed_price_usd_instead_of_raising(capsys):
    no_shows = [
        {"section": "101", "row": "1", "seat": "1", "price_usd": "100"},
        {"section": "101", "row": "1", "seat": "2", "price_usd": "N/A"},
    ]

    print_report(no_shows, no_shows, no_shows, "out.csv")

    out = capsys.readouterr().out
    assert "Total face value:   $100" in out
