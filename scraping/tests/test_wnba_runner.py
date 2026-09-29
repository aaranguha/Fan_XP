"""
Tests for wnba_runner.get_home_teams_tm() -- turns today's raw Ticketmaster
Discovery API event list into the list of (slug, game_time_utc_iso) tuples
to launch a runner for.

nfl_runner.py has a near-identical function that was hardened after two
real incidents (see scraping/tests/test_nfl_runner.py and CLAUDE.md §5):
  - A same-game duplicate listing (a base event plus a separate
    resale/VIP-package listing, both mentioning both team names) launched
    two subprocesses racing on the same game.log/pre_game.csv/
    .scrape_complete files -- confirmed live 2026-09-20/21 ("broncos"
    returned twice). Fixed in NFL by de-duping the result list while
    preserving first-seen order.
  - Non-game TM inventory (training camp, parking, hotel packages, club
    passes, suites) that still matches the classification+date query and
    happens to mention both team names needed explicit filtering. Fixed in
    NFL via TM_NOISE_TERMS.

wnba_runner.py's get_home_teams_tm() is a near-identical copy of the
*pre-fix* NFL function and never received either fix. The two xfail tests
at the bottom capture the correct (NFL-parity) behavior and document the
still-open bug without failing the suite -- see the companion GitHub
issue. (WNBA scraping is currently paused too, per CLAUDE.md §2.)

These tests drive get_home_teams_tm() through a monkeypatched requests.get
so no real network access or API key is needed.
"""

import pytest

import wnba_runner


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _tm_events(*names):
    return {"_embedded": {"events": [{"name": n} for n in names]}}


@pytest.fixture(autouse=True)
def tm_api_key(monkeypatch):
    monkeypatch.setenv("TICKETMASTER_API_KEY", "fake-key-for-tests")


def _patch_events(monkeypatch, *names):
    payload = _tm_events(*names)
    monkeypatch.setattr(
        wnba_runner.requests, "get", lambda *a, **k: FakeResponse(payload)
    )


def _slugs(results):
    return [slug for slug, _ in results]


# -- Basic matchup parsing ---------------------------------------------------

def test_finds_home_team_for_plain_matchup(monkeypatch):
    _patch_events(monkeypatch, "New York Liberty vs Las Vegas Aces")

    assert _slugs(wnba_runner.get_home_teams_tm("2026-07-13")) == ["liberty"]


def test_leftmost_team_name_wins_when_event_name_is_prefixed(monkeypatch):
    # A promo-prefixed listing shouldn't be assumed to start with the home
    # team -- whichever team name appears first in the string wins.
    _patch_events(
        monkeypatch,
        "Rivalry Night: Las Vegas Aces v New York Liberty",
    )

    assert _slugs(wnba_runner.get_home_teams_tm("2026-07-13")) == ["aces"]


def test_bare_single_team_listing_is_not_a_home_game(monkeypatch):
    _patch_events(monkeypatch, "Las Vegas Aces")

    assert wnba_runner.get_home_teams_tm("2026-07-13") == []


def test_multiple_distinct_home_games_all_returned(monkeypatch):
    _patch_events(
        monkeypatch,
        "New York Liberty vs Las Vegas Aces",
        "Seattle Storm vs Indiana Fever",
    )

    assert _slugs(wnba_runner.get_home_teams_tm("2026-07-13")) == ["liberty", "storm"]


def test_returns_game_time_alongside_slug(monkeypatch):
    payload = {
        "_embedded": {
            "events": [{
                "name": "New York Liberty vs Las Vegas Aces",
                "dates": {"start": {"dateTime": "2026-07-13T23:00:00Z"}},
            }]
        }
    }
    monkeypatch.setattr(wnba_runner.requests, "get", lambda *a, **k: FakeResponse(payload))

    assert wnba_runner.get_home_teams_tm("2026-07-13") == [("liberty", "2026-07-13T23:00:00Z")]


def test_missing_game_time_defaults_to_empty_string(monkeypatch):
    _patch_events(monkeypatch, "New York Liberty vs Las Vegas Aces")

    assert wnba_runner.get_home_teams_tm("2026-07-13") == [("liberty", "")]


# -- Missing API key ----------------------------------------------------------

def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("TICKETMASTER_API_KEY", raising=False)

    with pytest.raises(RuntimeError):
        wnba_runner.get_home_teams_tm("2026-07-13")


# -- Bugs: NFL's post-incident fixes were never ported to WNBA ---------------

@pytest.mark.xfail(
    reason=(
        "Real bug: nfl_runner.py's get_home_teams_tm() de-dupes its result "
        "list after a confirmed live incident (2026-09-20/21, runs "
        "35531541265/35549121946) where TM listed the same Broncos home game "
        "twice -- a base event plus a separate resale/VIP-package listing, "
        "both mentioning both team names -- and launched two subprocesses "
        "racing on the same game.log/pre_game.csv/.scrape_complete files. "
        "wnba_runner.py's get_home_teams_tm() has no equivalent de-dup step, "
        "so the same TM listing pattern would still double-launch "
        "wnba_run_game.py for one team's game. Not fixed here per QA-agent "
        "policy of not patching bugs it finds; see the companion GitHub "
        "issue."
    ),
    strict=True,
)
def test_duplicate_event_for_same_game_is_deduped_bug(monkeypatch):
    _patch_events(
        monkeypatch,
        "New York Liberty vs Las Vegas Aces",
        "New York Liberty vs Las Vegas Aces (VIP Package)",
    )

    assert _slugs(wnba_runner.get_home_teams_tm("2026-07-13")) == ["liberty"]


@pytest.mark.xfail(
    reason=(
        "Real bug: nfl_runner.py filters non-game TM inventory (training "
        "camp, parking, hotel packages, club passes, suites) via "
        "TM_NOISE_TERMS, added after confirming this kind of listing still "
        "matches classificationName=Football plus both team names on game "
        "day. wnba_runner.py's get_home_teams_tm() has no equivalent noise "
        "filter, so a hospitality/parking listing that happens to name both "
        "teams is counted as a real home game. Not fixed here per QA-agent "
        "policy of not patching bugs it finds; see the companion GitHub "
        "issue."
    ),
    strict=True,
)
def test_hotel_package_listing_is_not_a_home_game_bug(monkeypatch):
    _patch_events(monkeypatch, "New York Liberty vs Las Vegas Aces Hotel Package")

    assert wnba_runner.get_home_teams_tm("2026-07-13") == []
