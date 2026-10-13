"""
Tests for batch_capture_stadium_geometry.find_home_event_url() — picks a
team's next REAL home game out of a Ticketmaster keyword search, so the
geometry-capture script opens the right event and not a decoy.

This reuses the same "leftmost team name wins" home-team rule already
proven (and regression-tested) in nfl_runner.get_home_teams_tm(), plus two
rules specific to this function:
  - at least two team names must appear in the event (a bare single-team
    listing is non-game inventory: a suite package, a hotel bundle, etc.)
  - the event's venue city must match the team's real home city, so an
    NFL international game (London, Melbourne, ...) — which TM still lists
    with the "home" team named first for scheduling purposes — doesn't get
    mistaken for a capture target at the team's actual stadium.

Driven through a monkeypatched requests.get, so no real network access or
Ticketmaster API key is needed.
"""

import pytest

import batch_capture_stadium_geometry as bcsg


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _event(name, city, url="https://www.ticketmaster.com/event/abc123"):
    return {
        "name": name,
        "url": url,
        "_embedded": {"venues": [{"city": {"name": city}}]},
    }


def _patch_events(monkeypatch, *events):
    payload = {"_embedded": {"events": list(events)}}
    monkeypatch.setattr(bcsg.requests, "get", lambda *a, **k: FakeResponse(payload))


@pytest.fixture(autouse=True)
def tm_api_key(monkeypatch):
    monkeypatch.setenv("TICKETMASTER_API_KEY", "fake-key-for-tests")


# ── Home game found ───────────────────────────────────────────────────────

def test_finds_home_game_when_team_is_leftmost_and_city_matches(monkeypatch):
    _patch_events(
        monkeypatch,
        _event("San Francisco 49ers vs Dallas Cowboys", "Santa Clara"),
    )

    url = bcsg.find_home_event_url("San Francisco 49ers", "Santa Clara")

    assert url == "https://www.ticketmaster.com/event/abc123"


def test_leftmost_team_name_wins_on_prefixed_preseason_listing(monkeypatch):
    _patch_events(
        monkeypatch,
        _event(
            "Preseason Game 1: San Francisco 49ers v Las Vegas Raiders",
            "Santa Clara",
        ),
    )

    url = bcsg.find_home_event_url("San Francisco 49ers", "Santa Clara")

    assert url is not None


# ── Away games must not match ─────────────────────────────────────────────

def test_away_game_is_not_matched_as_home(monkeypatch):
    # The 49ers are mentioned, but second -- Dallas is the home team here.
    _patch_events(
        monkeypatch,
        _event("Dallas Cowboys vs San Francisco 49ers", "Arlington"),
    )

    assert bcsg.find_home_event_url("San Francisco 49ers", "Santa Clara") is None


# ── International games: home team leftmost, but wrong city ──────────────

def test_international_game_is_skipped_despite_team_listed_first(monkeypatch):
    # TM lists the "home" team first for scheduling purposes even for
    # games played abroad -- the city check is what catches this.
    _patch_events(
        monkeypatch,
        _event("Jacksonville Jaguars vs New England Patriots", "London"),
    )

    assert bcsg.find_home_event_url("Jacksonville Jaguars", "Jacksonville") is None


def test_city_match_is_substring_in_either_direction(monkeypatch):
    # Team config says "Miami Gardens", TM's venue city is just "Miami".
    _patch_events(
        monkeypatch,
        _event("Miami Dolphins vs New York Jets", "Miami"),
    )

    assert bcsg.find_home_event_url("Miami Dolphins", "Miami Gardens") is not None


def test_city_match_is_case_insensitive(monkeypatch):
    _patch_events(
        monkeypatch,
        _event("San Francisco 49ers vs Seattle Seahawks", "SANTA CLARA"),
    )

    assert bcsg.find_home_event_url("San Francisco 49ers", "santa clara") is not None


# ── Non-game inventory: fewer than two team names ─────────────────────────

def test_bare_single_team_listing_is_not_a_home_game(monkeypatch):
    _patch_events(
        monkeypatch,
        _event("San Francisco 49ers Suite Package", "Santa Clara"),
    )

    assert bcsg.find_home_event_url("San Francisco 49ers", "Santa Clara") is None


# ── Explicit noise filtering ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "noisy_name",
    [
        "San Francisco 49ers Training Camp vs Dallas Cowboys",
        "Parking: San Francisco 49ers vs Dallas Cowboys",
        "San Francisco 49ers vs Dallas Cowboys Hotel Package",
        "Not A Game Ticket - 49ers vs Cowboys",
        "San Francisco 49ers vs Dallas Cowboys Club Pass",
        "San Francisco 49ers vs Dallas Cowboys Suite",
    ],
)
def test_noise_listings_are_filtered_out(monkeypatch, noisy_name):
    _patch_events(monkeypatch, _event(noisy_name, "Santa Clara"))

    assert bcsg.find_home_event_url("San Francisco 49ers", "Santa Clara") is None


# ── URL sanity + no-match path ─────────────────────────────────────────────

def test_non_ticketmaster_url_is_not_returned(monkeypatch):
    _patch_events(
        monkeypatch,
        _event(
            "San Francisco 49ers vs Dallas Cowboys",
            "Santa Clara",
            url="https://example.com/some-other-link",
        ),
    )

    assert bcsg.find_home_event_url("San Francisco 49ers", "Santa Clara") is None


def test_no_events_returns_none(monkeypatch):
    _patch_events(monkeypatch)

    assert bcsg.find_home_event_url("San Francisco 49ers", "Santa Clara") is None


def test_first_qualifying_event_wins_when_multiple_present(monkeypatch):
    # TM's own query already sorts by date ascending, so the first event in
    # the list that passes all the filters is the *next* home game.
    _patch_events(
        monkeypatch,
        _event(
            "San Francisco 49ers Training Camp", "Santa Clara"
        ),  # filtered: noise
        _event(
            "Dallas Cowboys vs San Francisco 49ers", "Arlington"
        ),  # filtered: away game
        _event(
            "San Francisco 49ers vs Seattle Seahawks",
            "Santa Clara",
            url="https://www.ticketmaster.com/event/real-next-game",
        ),
        _event(
            "San Francisco 49ers vs Los Angeles Rams",
            "Santa Clara",
            url="https://www.ticketmaster.com/event/later-game",
        ),
    )

    url = bcsg.find_home_event_url("San Francisco 49ers", "Santa Clara")

    assert url == "https://www.ticketmaster.com/event/real-next-game"
