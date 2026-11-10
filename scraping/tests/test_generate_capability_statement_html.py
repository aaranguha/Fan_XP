"""
Tests for generate_capability_statement.py's generate_html() and main() --
the sales-collateral generator (capability_statement.html, repo root) that
pitch-readiness issue #5 flagged as going stale (CLAUDE.md §9). Before this,
only collect_proof_of_concept_stats() (test_generate_capability_statement.py)
had coverage; the string assembly that actually turns those stats into the
document a founder hands a team -- the NFL-teams-monitored count, the
comma/dollar number formatting, and main()'s file write -- had none.

generate_html() is deliberately fully generic (CLAUDE.md §4.4: "explicitly
requested after an earlier version was Commanders-specific and had gone
stale") -- these tests also guard against a team name creeping back in.
"""

import re

import generate_capability_statement as gcs


FAKE_NFL_TEAMS = {f"team{i}": {"slug": f"team{i}"} for i in range(32)}


def _stats(games=0, no_shows=0, value=0.0):
    return {"games": games, "no_shows": no_shows, "value": value}


# ── generate_html() ──────────────────────────────────────────────────────────

def test_nfl_teams_monitored_count_reflects_nfl_teams_module(monkeypatch):
    monkeypatch.setattr(gcs, "NFL_TEAMS", FAKE_NFL_TEAMS)
    monkeypatch.setattr(gcs, "collect_proof_of_concept_stats", lambda: _stats())

    html = gcs.generate_html()

    assert ">32<" in html
    assert "Already monitoring 32 NFL teams" in html


def test_poc_stats_are_rendered_with_thousands_separators(monkeypatch):
    monkeypatch.setattr(gcs, "NFL_TEAMS", FAKE_NFL_TEAMS)
    monkeypatch.setattr(
        gcs, "collect_proof_of_concept_stats",
        lambda: _stats(games=14, no_shows=12345, value=98765.0),
    )

    html = gcs.generate_html()

    assert ">14<" in html
    assert ">12,345<" in html
    assert ">$98,765<" in html


def test_poc_value_rounds_to_whole_dollars(monkeypatch):
    # The template uses f"${value:,.0f}" -- cents must not leak into the
    # pitch document as a stray decimal.
    monkeypatch.setattr(gcs, "NFL_TEAMS", FAKE_NFL_TEAMS)
    monkeypatch.setattr(
        gcs, "collect_proof_of_concept_stats",
        lambda: _stats(games=1, no_shows=1, value=1234567.89),
    )

    html = gcs.generate_html()

    assert ">$1,234,568<" in html
    assert "1234567.89" not in html


def test_zeroed_stats_render_as_literal_zeros_not_a_crash(monkeypatch):
    # The real no-client / no-data path (collect_proof_of_concept_stats()
    # already covers returning zeros) must still produce a valid document,
    # not a KeyError or a formatting exception, since this runs unattended.
    monkeypatch.setattr(gcs, "NFL_TEAMS", FAKE_NFL_TEAMS)
    monkeypatch.setattr(gcs, "collect_proof_of_concept_stats", lambda: _stats())

    html = gcs.generate_html()

    assert ">0<" in html
    assert ">$0<" in html


def test_output_is_a_complete_html_document(monkeypatch):
    monkeypatch.setattr(gcs, "NFL_TEAMS", FAKE_NFL_TEAMS)
    monkeypatch.setattr(gcs, "collect_proof_of_concept_stats", lambda: _stats())

    html = gcs.generate_html()

    assert html.startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")
    assert html.count("<html") == 1
    assert html.count("</html>") == 1


def test_contains_todays_date_in_long_form(monkeypatch):
    monkeypatch.setattr(gcs, "NFL_TEAMS", FAKE_NFL_TEAMS)
    monkeypatch.setattr(gcs, "collect_proof_of_concept_stats", lambda: _stats())

    html = gcs.generate_html()

    # e.g. "November 10, 2026" -- %B %-d, %Y with no leading zero on the day.
    assert re.search(r"Capability Statement\s*&nbsp;&middot;&nbsp;\s*[A-Z][a-z]+ \d{1,2}, \d{4}", html)
    assert re.search(r"[A-Z][a-z]+ 0\d,", html) is None


def test_is_team_agnostic(monkeypatch):
    # Explicit regression per CLAUDE.md §4.4: an earlier version was
    # Commanders-specific and had gone stale -- this must stay generic.
    monkeypatch.setattr(gcs, "NFL_TEAMS", FAKE_NFL_TEAMS)
    monkeypatch.setattr(gcs, "collect_proof_of_concept_stats", lambda: _stats())

    html = gcs.generate_html()

    for team_name in ("Commanders", "Washington"):
        assert team_name not in html


def test_contains_founder_contact_and_live_site_link(monkeypatch):
    monkeypatch.setattr(gcs, "NFL_TEAMS", FAKE_NFL_TEAMS)
    monkeypatch.setattr(gcs, "collect_proof_of_concept_stats", lambda: _stats())

    html = gcs.generate_html()

    assert "aaranguhaca@gmail.com" in html
    assert "fan-xp.vercel.app" in html


# ── main() ────────────────────────────────────────────────────────────────────

def test_main_writes_generate_html_output_one_directory_up(monkeypatch, tmp_path):
    repo_root = tmp_path
    scraping_dir = repo_root / "scraping"
    scraping_dir.mkdir()
    monkeypatch.chdir(scraping_dir)

    monkeypatch.setattr(gcs, "NFL_TEAMS", FAKE_NFL_TEAMS)
    monkeypatch.setattr(gcs, "collect_proof_of_concept_stats", lambda: _stats(games=3, no_shows=42, value=500.0))

    gcs.main()

    out_file = repo_root / "capability_statement.html"
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert ">42<" in content
    assert content.startswith("<!DOCTYPE html>")


def test_main_overwrites_a_stale_existing_file(monkeypatch, tmp_path):
    repo_root = tmp_path
    scraping_dir = repo_root / "scraping"
    scraping_dir.mkdir()
    (repo_root / "capability_statement.html").write_text("<html>stale commanders-only draft</html>", encoding="utf-8")
    monkeypatch.chdir(scraping_dir)

    monkeypatch.setattr(gcs, "NFL_TEAMS", FAKE_NFL_TEAMS)
    monkeypatch.setattr(gcs, "collect_proof_of_concept_stats", lambda: _stats())

    gcs.main()

    content = (repo_root / "capability_statement.html").read_text(encoding="utf-8")
    assert "stale" not in content
    assert content.startswith("<!DOCTYPE html>")
