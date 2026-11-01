"""
Tests for nfl_run_game.py's run_snapshot() — had NO coverage before this file,
despite being the function that decides HOW a snapshot's rows get built and
whether a game even gets re-scraped at all.

Covers:
  - The embedded_offers/places_facets/per-facet fallback chain. CLAUDE.md §4.1
    documents this as the single most important fact about the scraping
    pipeline: Ticketmaster removed the "section" field from the inventory/
    places facets responses (~Sept 2026), which silently broke the old
    facet-matching join and returned 0 priced rows out of 1228. The fix made
    build_rows_from_embedded_offers() (fed by embedded_offers) the PRIMARY
    path, with parse_seats()/parse_facet() now fallbacks only. A regression
    here — e.g. preferring places_facets over a present embedded_offers, or
    vice versa in the wrong direction — would silently resurrect that bug.
  - The "out_csv already exists" short-circuit: a game that's already been
    scraped for this snapshot must load the existing CSV and must NOT
    re-scrape (scrape_listings must not even be called), which is what keeps
    an overlapping cron trigger or a re-run on the same day from hitting
    Ticketmaster twice for data that's already on disk.
"""

import os

import nfl_run_game


def _event():
    return {
        "name": "Kansas City Chiefs vs. Denver Broncos",
        "dates": {"start": {"localDate": "2026-09-13"}},
        "_embedded": {"venues": [{"name": "Arrowhead Stadium"}]},
    }


def _patch_scrape_listings(monkeypatch, facets=None, offer_price_map=None,
                            places_facets=None, embedded_offers=None):
    calls = []

    def fake_scrape_listings(url, max_retries=1, team_slug="default"):
        calls.append({"url": url, "max_retries": max_retries, "team_slug": team_slug})
        return (facets or [], offer_price_map or {}, places_facets or [], embedded_offers or [])

    monkeypatch.setattr(nfl_run_game, "scrape_listings", fake_scrape_listings)
    return calls


def _silence_io(monkeypatch):
    # save_csv/print_summary just touch disk/stdout — not what's under test.
    monkeypatch.setattr(nfl_run_game, "save_csv", lambda rows, path: None)
    monkeypatch.setattr(nfl_run_game, "print_summary", lambda event, rows, out_csv: None)


# ── embedded_offers is the primary path ──────────────────────────────────────

def test_embedded_offers_present_takes_priority_over_places_facets(monkeypatch, tmp_path):
    # Regression guard for CLAUDE.md §4.1: once embedded_offers is available,
    # it must be used even if places_facets is also non-empty — the old
    # facet-matching path is a fallback only, not a competing primary path.
    _patch_scrape_listings(
        monkeypatch,
        facets=[{"some": "facet"}],
        places_facets=[{"some": "place"}],
        embedded_offers=[{"section": "101", "row": "A", "seatFrom": "1", "seatTo": "1", "price": 50}],
    )
    _silence_io(monkeypatch)

    built_from = []
    monkeypatch.setattr(
        nfl_run_game, "build_rows_from_embedded_offers",
        lambda embedded_offers, scraped_at: built_from.append("embedded") or [{"section": "101"}],
    )
    monkeypatch.setattr(
        nfl_run_game, "parse_seats",
        lambda *a, **kw: built_from.append("places") or [],
    )
    monkeypatch.setattr(
        nfl_run_game, "parse_facet",
        lambda *a, **kw: built_from.append("facet") or [],
    )

    out_csv = str(tmp_path / "pre_game.csv")
    rows = nfl_run_game.run_snapshot(_event(), "https://tm.example/event", "pre_game", out_csv, "chiefs")

    assert built_from == ["embedded"]
    assert rows == [{"section": "101"}]


def test_no_embedded_offers_falls_back_to_places_facets(monkeypatch, tmp_path):
    _patch_scrape_listings(
        monkeypatch,
        facets=[{"some": "facet"}],
        places_facets=[{"some": "place"}],
        embedded_offers=[],  # falsy -- must not take the primary path
    )
    _silence_io(monkeypatch)

    built_from = []
    monkeypatch.setattr(
        nfl_run_game, "build_rows_from_embedded_offers",
        lambda *a, **kw: built_from.append("embedded") or [],
    )
    monkeypatch.setattr(
        nfl_run_game, "parse_seats",
        lambda *a, **kw: built_from.append("places") or [{"section": "202"}],
    )
    monkeypatch.setattr(
        nfl_run_game, "parse_facet",
        lambda *a, **kw: built_from.append("facet") or [],
    )

    out_csv = str(tmp_path / "pre_game.csv")
    rows = nfl_run_game.run_snapshot(_event(), "https://tm.example/event", "pre_game", out_csv, "chiefs")

    assert built_from == ["places"]
    assert rows == [{"section": "202"}]


def test_no_embedded_offers_and_no_places_facets_falls_back_to_per_facet_parsing(monkeypatch, tmp_path):
    # The last-resort path: iterate every facet individually through
    # parse_facet() and concatenate the results.
    _patch_scrape_listings(
        monkeypatch,
        facets=[{"id": "f1"}, {"id": "f2"}],
        places_facets=[],
        embedded_offers=[],
    )
    _silence_io(monkeypatch)

    monkeypatch.setattr(nfl_run_game, "build_rows_from_embedded_offers", lambda *a, **kw: [])
    monkeypatch.setattr(nfl_run_game, "parse_seats", lambda *a, **kw: [])

    parsed_facets = []

    def fake_parse_facet(facet, offer_price_map, scraped_at):
        parsed_facets.append(facet["id"])
        return [{"section": facet["id"]}]

    monkeypatch.setattr(nfl_run_game, "parse_facet", fake_parse_facet)

    out_csv = str(tmp_path / "pre_game.csv")
    rows = nfl_run_game.run_snapshot(_event(), "https://tm.example/event", "pre_game", out_csv, "chiefs")

    assert parsed_facets == ["f1", "f2"]
    assert rows == [{"section": "f1"}, {"section": "f2"}]


def test_empty_facets_and_no_fallback_data_returns_empty_list(monkeypatch, tmp_path):
    _patch_scrape_listings(monkeypatch, facets=[], places_facets=[], embedded_offers=[])
    _silence_io(monkeypatch)
    monkeypatch.setattr(nfl_run_game, "build_rows_from_embedded_offers", lambda *a, **kw: [])
    monkeypatch.setattr(nfl_run_game, "parse_seats", lambda *a, **kw: [])
    monkeypatch.setattr(nfl_run_game, "parse_facet", lambda *a, **kw: [])

    out_csv = str(tmp_path / "pre_game.csv")
    rows = nfl_run_game.run_snapshot(_event(), "https://tm.example/event", "pre_game", out_csv, "chiefs")

    assert rows == []


# ── existing-CSV short-circuit ───────────────────────────────────────────────

def test_existing_csv_is_loaded_without_rescraping(monkeypatch, tmp_path):
    out_csv = tmp_path / "halftime.csv"
    out_csv.write_text("section,row,seat\n101,A,1\n101,A,2\n")

    scrape_calls = _patch_scrape_listings(monkeypatch)
    loaded = []
    monkeypatch.setattr(
        nfl_run_game, "load_csv",
        lambda path: loaded.append(path) or [{"section": "101", "row": "A", "seat": "1"},
                                              {"section": "101", "row": "A", "seat": "2"}],
    )

    rows = nfl_run_game.run_snapshot(_event(), "https://tm.example/event", "halftime", str(out_csv), "chiefs")

    assert scrape_calls == []  # scrape_listings must never be called
    assert loaded == [str(out_csv)]
    assert len(rows) == 2


def test_missing_csv_does_scrape(monkeypatch, tmp_path):
    out_csv = tmp_path / "halftime.csv"
    assert not out_csv.exists()

    scrape_calls = _patch_scrape_listings(
        monkeypatch, embedded_offers=[{"section": "101", "row": "A", "seatFrom": "1", "seatTo": "1", "price": 50}],
    )
    _silence_io(monkeypatch)
    monkeypatch.setattr(
        nfl_run_game, "build_rows_from_embedded_offers",
        lambda *a, **kw: [{"section": "101"}],
    )

    rows = nfl_run_game.run_snapshot(_event(), "https://tm.example/event", "halftime", str(out_csv), "chiefs")

    assert len(scrape_calls) == 1
    assert scrape_calls[0]["url"] == "https://tm.example/event"
    assert scrape_calls[0]["team_slug"] == "chiefs"
    assert rows == [{"section": "101"}]
