"""
Tests for nfl_runner.get_home_teams_tm() — turns today's raw Ticketmaster
Discovery API event list into the list of team slugs to launch a runner for.

This function's matching/dedup logic has caused two real incidents:
  - A same-game duplicate slug (TM lists a base event *and* a separate
    resale/VIP-package listing for the same game, both mentioning both team
    names) launched two subprocesses racing on the same game.log/pre_game.csv
    /.scrape_complete files (confirmed live 2026-09-20/21, runs
    35531541265/35549121946: "broncos" returned twice).
  - Non-game TM inventory (training camp, parking, hotel packages) that still
    matches classificationName=Football and mentions team names needed
    explicit filtering to avoid spawning bogus runs.

These tests drive get_home_teams_tm() through a monkeypatched requests.get
so no real network access or API key is needed.
"""

import pytest

import nfl_runner


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
        nfl_runner.requests, "get", lambda *a, **k: FakeResponse(payload)
    )


# ── Basic matchup parsing ────────────────────────────────────────────────

def test_finds_home_team_for_plain_matchup(monkeypatch):
    _patch_events(monkeypatch, "Kansas City Chiefs vs Denver Broncos")

    assert nfl_runner.get_home_teams_tm("2026-09-13") == ["chiefs"]


def test_leftmost_team_name_wins_on_prefixed_preseason_listing(monkeypatch):
    # Preseason events are often prefixed ("Preseason Game 1: ...") so the
    # home team can't be assumed to start the string — it's whichever team
    # name appears first.
    _patch_events(
        monkeypatch,
        "Preseason Game 1: Pittsburgh Steelers v Green Bay Packers",
    )

    assert nfl_runner.get_home_teams_tm("2026-08-09") == ["steelers"]


def test_bare_single_team_listing_is_not_a_home_game(monkeypatch):
    # No opponent mentioned at all -- must not match as a home game.
    _patch_events(monkeypatch, "New York Jets")

    assert nfl_runner.get_home_teams_tm("2026-09-14") == []


# ── Noise filtering ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "noisy_name",
    [
        "Denver Broncos Training Camp presented by Kansas City Chiefs Fans",
        "Kansas City Chiefs Parking: vs Denver Broncos",
        "Kansas City Chiefs vs Denver Broncos Hotel Package",
        "Kansas City Chiefs vs Denver Broncos - Not A Game Ticket",
        "Kansas City Chiefs vs Denver Broncos Club Pass",
    ],
)
def test_non_game_inventory_is_filtered_out(monkeypatch, noisy_name):
    _patch_events(monkeypatch, noisy_name)

    assert nfl_runner.get_home_teams_tm("2026-09-13") == []


# ── Duplicate-listing dedup (the confirmed 2026-09-20/21 incident) ───────

def test_duplicate_event_for_same_game_is_deduped(monkeypatch):
    # TM listed the same Broncos home game twice (base event + a separate
    # resale/VIP listing) -- both mention both team names and would
    # otherwise launch two runner subprocesses for the same game.
    _patch_events(
        monkeypatch,
        "Denver Broncos vs Las Vegas Raiders",
        "Denver Broncos vs Las Vegas Raiders (VIP Package)",
    )

    assert nfl_runner.get_home_teams_tm("2026-09-20") == ["broncos"]


def test_dedup_preserves_first_seen_order(monkeypatch):
    _patch_events(
        monkeypatch,
        "Denver Broncos vs Las Vegas Raiders",
        "Kansas City Chiefs vs Buffalo Bills",
        "Denver Broncos vs Las Vegas Raiders (VIP Package)",
    )

    assert nfl_runner.get_home_teams_tm("2026-09-20") == ["broncos", "chiefs"]


# ── Multiple distinct home games in one day ──────────────────────────────

def test_multiple_distinct_home_games_all_returned(monkeypatch):
    _patch_events(
        monkeypatch,
        "Kansas City Chiefs vs Denver Broncos",
        "New York Giants vs Philadelphia Eagles",
    )

    assert nfl_runner.get_home_teams_tm("2026-09-13") == ["chiefs", "giants"]


# ── Missing API key ───────────────────────────────────────────────────────

def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("TICKETMASTER_API_KEY", raising=False)

    with pytest.raises(RuntimeError):
        nfl_runner.get_home_teams_tm("2026-09-13")
