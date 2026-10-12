"""
Tests for fetch_seatgeek.py -- the SeatGeek API fallback path used for MLB
teams not listed on Ticketmaster (see its own module docstring). MLB
scraping is currently paused (CLAUDE.md §2), but this is still real,
live code that will run again the moment MLB is re-enabled, and it has
never had direct test coverage.

Covers:
  - _params(): client_id is only added when SG_CLIENT_ID is actually set,
    and per_page/extra merging.
  - find_seatgeek_event(): the "prefer the event listing our team as a
    home performer, else fall back to the first result" selection logic,
    plus its three RuntimeError guards (no client id configured, no
    SeatGeek slug mapped for the team, no events found at all).
  - fetch_seatgeek_listings(): row shape and defaulting when a listing is
    missing fields (id/section/row/quantity/price), matching the
    parse_facet()-compatible contract its docstring promises.
  - scrape_seatgeek(): end-to-end wiring, including the
    datetime.fromisoformat() ValueError fallback to first_pitch_utc=None
    for an unparseable datetime_utc value.

All network access is mocked via a monkeypatched `requests.get` -- no
real network access or SEATGEEK_CLIENT_ID is used or needed.
"""

import pytest

import fetch_seatgeek


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def clear_client_id(monkeypatch):
    # SG_CLIENT_ID is read from the environment once at import time, so
    # tests drive it by patching the already-loaded module attribute
    # directly, not os.environ.
    monkeypatch.setattr(fetch_seatgeek, "SG_CLIENT_ID", "")


def _patch_get(monkeypatch, payload):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append({"url": url, "params": params, "timeout": timeout})
        return FakeResponse(payload)

    monkeypatch.setattr(fetch_seatgeek.requests, "get", fake_get)
    return calls


# ── _params() ─────────────────────────────────────────────────────────────

def test_params_omits_client_id_when_not_configured():
    params = fetch_seatgeek._params()
    assert params == {"per_page": 5000}


def test_params_includes_client_id_when_configured(monkeypatch):
    monkeypatch.setattr(fetch_seatgeek, "SG_CLIENT_ID", "fake-client-id")
    params = fetch_seatgeek._params()
    assert params["client_id"] == "fake-client-id"
    assert params["per_page"] == 5000


def test_params_merges_extra_and_can_override_per_page():
    params = fetch_seatgeek._params({"event_id": 123, "per_page": 10})
    assert params == {"per_page": 10, "event_id": 123}


def test_params_extra_default_is_not_mutated_across_calls():
    # The function signature uses a mutable default dict (`extra: dict = {}`)
    # -- a classic Python footgun. Calling _params() repeatedly must never
    # leak keys from one call's `extra` into a later call that passes none.
    fetch_seatgeek._params({"event_id": 999})
    params = fetch_seatgeek._params()
    assert "event_id" not in params


# ── find_seatgeek_event() ───────────────────────────────────────────────────

def test_find_event_raises_without_client_id(monkeypatch):
    with pytest.raises(RuntimeError, match="SEATGEEK_CLIENT_ID"):
        fetch_seatgeek.find_seatgeek_event("dodgers", "2026-07-04")


def test_find_event_raises_for_unmapped_team_slug(monkeypatch):
    monkeypatch.setattr(fetch_seatgeek, "SG_CLIENT_ID", "fake-client-id")
    with pytest.raises(RuntimeError, match="No SeatGeek slug configured"):
        fetch_seatgeek.find_seatgeek_event("not-a-real-team", "2026-07-04")


def test_find_event_raises_when_no_events_returned(monkeypatch):
    monkeypatch.setattr(fetch_seatgeek, "SG_CLIENT_ID", "fake-client-id")
    _patch_get(monkeypatch, {"events": []})
    with pytest.raises(RuntimeError, match="No SeatGeek event found"):
        fetch_seatgeek.find_seatgeek_event("dodgers", "2026-07-04")


def test_find_event_prefers_home_performer_match(monkeypatch):
    monkeypatch.setattr(fetch_seatgeek, "SG_CLIENT_ID", "fake-client-id")
    # Away game listed first, home game listed second -- the home-performer
    # match must win even though it isn't events[0].
    payload = {
        "events": [
            {
                "id": 1,
                "title": "Away game",
                "performers": [{"slug": "san-francisco-giants"}, {"slug": "some-other-team"}],
            },
            {
                "id": 2,
                "title": "Home game",
                "performers": [{"slug": "los-angeles-dodgers"}, {"slug": "san-francisco-giants"}],
            },
        ]
    }
    _patch_get(monkeypatch, payload)

    event = fetch_seatgeek.find_seatgeek_event("dodgers", "2026-07-04")

    assert event["id"] == 2


def test_find_event_falls_back_to_first_event_when_no_home_match(monkeypatch):
    monkeypatch.setattr(fetch_seatgeek, "SG_CLIENT_ID", "fake-client-id")
    payload = {
        "events": [
            {"id": 1, "title": "Neither event lists dodgers as a performer", "performers": [{"slug": "team-a"}]},
            {"id": 2, "title": "Second event", "performers": [{"slug": "team-b"}]},
        ]
    }
    _patch_get(monkeypatch, payload)

    event = fetch_seatgeek.find_seatgeek_event("dodgers", "2026-07-04")

    assert event["id"] == 1


def test_find_event_handles_performers_missing_slug_key(monkeypatch):
    # A performer dict without a "slug" key must not raise -- p.get("slug", "")
    # is the documented defensive default.
    monkeypatch.setattr(fetch_seatgeek, "SG_CLIENT_ID", "fake-client-id")
    payload = {"events": [{"id": 1, "title": "Weird event", "performers": [{"name": "no slug here"}]}]}
    _patch_get(monkeypatch, payload)

    event = fetch_seatgeek.find_seatgeek_event("dodgers", "2026-07-04")

    assert event["id"] == 1


def test_find_event_sends_expected_query_params(monkeypatch):
    monkeypatch.setattr(fetch_seatgeek, "SG_CLIENT_ID", "fake-client-id")
    calls = _patch_get(monkeypatch, {"events": [{"id": 1, "performers": []}]})

    fetch_seatgeek.find_seatgeek_event("dodgers", "2026-07-04")

    assert len(calls) == 1
    params = calls[0]["params"]
    assert params["performers.slug"] == "los-angeles-dodgers"
    assert params["datetime_local.gte"] == "2026-07-04T00:00:00"
    assert params["datetime_local.lte"] == "2026-07-04T23:59:59"
    assert params["type"] == "mlb"
    assert params["client_id"] == "fake-client-id"


# ── fetch_seatgeek_listings() ───────────────────────────────────────────────

def test_fetch_listings_maps_full_listing(monkeypatch):
    payload = {
        "listings": [
            {
                "id": 987,
                "section": "114",
                "row": "12",
                "quantity": 2,
                "price": {"amount": 45.5},
            }
        ]
    }
    _patch_get(monkeypatch, payload)

    rows = fetch_seatgeek.fetch_seatgeek_listings(event_id=555, scraped_at="2026-07-04T18:00:00Z")

    assert rows == [
        {
            "listing_id": "987",
            "section": "114",
            "row": "12",
            "qty": 2,
            "price_usd": 45.5,
            "scraped_at": "2026-07-04T18:00:00Z",
            "source": "seatgeek",
        }
    ]


def test_fetch_listings_defaults_missing_fields(monkeypatch):
    # A listing missing every optional field must still produce a row with
    # the documented defaults, not raise a KeyError.
    _patch_get(monkeypatch, {"listings": [{}]})

    rows = fetch_seatgeek.fetch_seatgeek_listings(event_id=555, scraped_at="2026-07-04T18:00:00Z")

    assert rows == [
        {
            "listing_id": "",
            "section": "",
            "row": "",
            "qty": 0,
            "price_usd": 0,
            "scraped_at": "2026-07-04T18:00:00Z",
            "source": "seatgeek",
        }
    ]


def test_fetch_listings_empty_list_returns_empty_rows(monkeypatch):
    _patch_get(monkeypatch, {"listings": []})

    rows = fetch_seatgeek.fetch_seatgeek_listings(event_id=555, scraped_at="2026-07-04T18:00:00Z")

    assert rows == []


def test_fetch_listings_sends_event_id_param(monkeypatch):
    calls = _patch_get(monkeypatch, {"listings": []})

    fetch_seatgeek.fetch_seatgeek_listings(event_id=555, scraped_at="2026-07-04T18:00:00Z")

    assert calls[0]["params"]["event_id"] == 555


# ── scrape_seatgeek() ────────────────────────────────────────────────────────

def test_scrape_seatgeek_end_to_end(monkeypatch):
    monkeypatch.setattr(fetch_seatgeek, "SG_CLIENT_ID", "fake-client-id")

    responses = [
        {
            "events": [
                {
                    "id": 42,
                    "title": "Giants vs. Dodgers",
                    "performers": [{"slug": "los-angeles-dodgers"}],
                    "datetime_local": "2026-07-04T19:10:00",
                    "datetime_utc": "2026-07-05T02:10:00Z",
                    "venue": {"name": "Dodger Stadium", "city": "Los Angeles"},
                    "url": "https://seatgeek.com/giants-at-dodgers-tickets/2026-07-04/42",
                }
            ]
        },
        {"listings": [{"id": 1, "section": "100", "row": "A", "quantity": 1, "price": {"amount": 100}}]},
    ]

    def fake_get(url, params=None, timeout=None):
        return FakeResponse(responses.pop(0))

    monkeypatch.setattr(fetch_seatgeek.requests, "get", fake_get)

    event_meta, rows = fetch_seatgeek.scrape_seatgeek("dodgers", "2026-07-04")

    assert event_meta["sg_event_id"] == 42
    assert event_meta["name"] == "Giants vs. Dodgers"
    assert event_meta["venue"] == "Dodger Stadium"
    assert event_meta["city"] == "Los Angeles"
    assert event_meta["game_date"] == "2026-07-04"
    assert event_meta["first_pitch_utc"].year == 2026
    assert event_meta["first_pitch_utc"].month == 7
    assert event_meta["first_pitch_utc"].day == 5
    assert len(rows) == 1
    assert rows[0]["source"] == "seatgeek"


def test_scrape_seatgeek_falls_back_to_none_for_unparseable_datetime(monkeypatch):
    monkeypatch.setattr(fetch_seatgeek, "SG_CLIENT_ID", "fake-client-id")

    responses = [
        {
            "events": [
                {
                    "id": 42,
                    "title": "Giants vs. Dodgers",
                    "performers": [{"slug": "los-angeles-dodgers"}],
                    "datetime_local": "2026-07-04T19:10:00",
                    "datetime_utc": "not-a-real-timestamp",
                    "venue": {"name": "Dodger Stadium", "city": "Los Angeles"},
                }
            ]
        },
        {"listings": []},
    ]

    def fake_get(url, params=None, timeout=None):
        return FakeResponse(responses.pop(0))

    monkeypatch.setattr(fetch_seatgeek.requests, "get", fake_get)

    event_meta, rows = fetch_seatgeek.scrape_seatgeek("dodgers", "2026-07-04")

    assert event_meta["first_pitch_utc"] is None
    assert rows == []


def test_scrape_seatgeek_builds_fallback_url_when_missing(monkeypatch):
    monkeypatch.setattr(fetch_seatgeek, "SG_CLIENT_ID", "fake-client-id")

    responses = [
        {
            "events": [
                {
                    "id": 42,
                    "title": "Giants vs. Dodgers",
                    "performers": [{"slug": "los-angeles-dodgers"}],
                    "datetime_local": "2026-07-04T19:10:00",
                    "datetime_utc": "2026-07-05T02:10:00Z",
                    "venue": {"name": "Dodger Stadium", "city": "Los Angeles"},
                    # no "url" key
                }
            ]
        },
        {"listings": []},
    ]

    def fake_get(url, params=None, timeout=None):
        return FakeResponse(responses.pop(0))

    monkeypatch.setattr(fetch_seatgeek.requests, "get", fake_get)

    event_meta, _ = fetch_seatgeek.scrape_seatgeek("dodgers", "2026-07-04")

    assert event_meta["url"] == "https://seatgeek.com/42"
