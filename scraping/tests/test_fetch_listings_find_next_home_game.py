"""
Tests for fetch_listings.find_next_home_game()'s branches not already
covered by test_fetch_listings.py (which only exercises the add-on/noise
filtering). This function is what every MLB/WNBA/NBA runner calls to pick
which TM event to actually scrape -- picking the wrong one means scraping
an empty/irrelevant event for the whole game.

Covers:
  - the game_date / localDate filter (kept vs dropped, and the "no match"
    RuntimeError)
  - the G-League-affiliate guard: an event whose name starts with the
    team's own keyword wins over one that merely mentions it (e.g. a
    shared-keyword affiliate team), falling back to the first remaining
    event only when nothing starts with the keyword
  - the two other RuntimeError paths: no events returned at all, and a
    missing TICKETMASTER_API_KEY
"""

import pytest

import fetch_listings


def _event(name, local_date="2026-09-27"):
    return {"name": name, "dates": {"start": {"localDate": local_date}}}


def _stub_tm(monkeypatch, events):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"_embedded": {"events": events}}

    monkeypatch.setattr(fetch_listings, "TM_API_KEY", "test")
    monkeypatch.setattr(fetch_listings.requests, "get", lambda *a, **k: Resp())


# ── game_date / localDate filter ────────────────────────────────────────────

def test_event_on_requested_date_is_kept(monkeypatch):
    _stub_tm(monkeypatch, [
        _event("Golden State Warriors vs. Los Angeles Lakers", "2026-09-27"),
    ])

    e = fetch_listings.find_next_home_game("Golden State Warriors", "2026-09-27", "Basketball")

    assert e["name"] == "Golden State Warriors vs. Los Angeles Lakers"


def test_event_on_a_different_date_is_filtered_out(monkeypatch):
    _stub_tm(monkeypatch, [
        _event("Golden State Warriors vs. Los Angeles Lakers", "2026-10-05"),
    ])

    with pytest.raises(RuntimeError, match="2026-09-27"):
        fetch_listings.find_next_home_game("Golden State Warriors", "2026-09-27", "Basketball")


def test_only_the_matching_date_survives_a_mixed_list(monkeypatch):
    _stub_tm(monkeypatch, [
        _event("Golden State Warriors vs. Los Angeles Lakers", "2026-10-05"),
        _event("Golden State Warriors vs. Phoenix Suns", "2026-09-27"),
    ])

    e = fetch_listings.find_next_home_game("Golden State Warriors", "2026-09-27", "Basketball")

    assert e["name"] == "Golden State Warriors vs. Phoenix Suns"


def test_no_game_date_given_skips_the_date_filter_entirely(monkeypatch):
    _stub_tm(monkeypatch, [
        _event("Golden State Warriors vs. Los Angeles Lakers", "2026-10-05"),
    ])

    e = fetch_listings.find_next_home_game("Golden State Warriors", None, "Basketball")

    assert e["name"] == "Golden State Warriors vs. Los Angeles Lakers"


# ── G-League / shared-keyword affiliate guard ───────────────────────────────

def test_event_name_starting_with_the_team_keyword_wins_over_an_earlier_listed_affiliate(monkeypatch):
    # TM can return a G-League affiliate's game (named after its own city,
    # but still matching the parent team's keyword search) ahead of the
    # real NBA game in the results. The startswith check is what rescues
    # this: it's checked against *every* event, so order doesn't matter.
    _stub_tm(monkeypatch, [
        _event("Santa Cruz Warriors vs. Stockton Kings"),
        _event("Golden State Warriors vs. Phoenix Suns"),
    ])

    e = fetch_listings.find_next_home_game("Golden State Warriors", "2026-09-27", "Basketball")

    assert e["name"] == "Golden State Warriors vs. Phoenix Suns"


def test_falls_back_to_first_remaining_event_when_none_start_with_the_keyword(monkeypatch):
    # Neither event's name starts with the keyword (e.g. TM prefixed both
    # with "Preseason Game 1: ..."), so there's nothing to rescue -- the
    # first (date-sorted) event is used as-is.
    _stub_tm(monkeypatch, [
        _event("Preseason Game 1: Golden State Warriors vs. Lakers"),
        _event("Preseason Game 2: Golden State Warriors vs. Suns"),
    ])

    e = fetch_listings.find_next_home_game("Golden State Warriors", "2026-09-27", "Basketball")

    assert e["name"] == "Preseason Game 1: Golden State Warriors vs. Lakers"


def test_keyword_match_is_case_insensitive(monkeypatch):
    _stub_tm(monkeypatch, [
        _event("golden state warriors vs. Phoenix Suns"),
    ])

    e = fetch_listings.find_next_home_game("Golden State Warriors", "2026-09-27", "Basketball")

    assert e["name"] == "golden state warriors vs. Phoenix Suns"


def test_non_game_add_on_is_dropped_before_the_affiliate_guard_runs(monkeypatch):
    # The add-on filter runs first: a club-pass event that happens to start
    # with the team keyword must still be dropped, not returned just
    # because it would otherwise "win" the startswith check.
    _stub_tm(monkeypatch, [
        _event("Golden State Warriors 1933 Club Pass (NOT A GAME TICKET)"),
        _event("Golden State Warriors vs. Phoenix Suns"),
    ])

    e = fetch_listings.find_next_home_game("Golden State Warriors", "2026-09-27", "Basketball")

    assert e["name"] == "Golden State Warriors vs. Phoenix Suns"


# ── remaining RuntimeError paths ────────────────────────────────────────────

def test_no_events_returned_at_all_raises(monkeypatch):
    _stub_tm(monkeypatch, [])

    with pytest.raises(RuntimeError):
        fetch_listings.find_next_home_game("Golden State Warriors", "2026-09-27", "Basketball")


def test_missing_api_key_raises_before_any_request(monkeypatch):
    monkeypatch.setattr(fetch_listings, "TM_API_KEY", "")
    called = []
    monkeypatch.setattr(fetch_listings.requests, "get", lambda *a, **k: called.append(1))

    with pytest.raises(RuntimeError):
        fetch_listings.find_next_home_game("Golden State Warriors", "2026-09-27", "Basketball")

    assert called == []
