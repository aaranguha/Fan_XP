"""
Tests for fetch_listings.refresh_endpoints() -- the pre-halftime "warm the
endpoints cache" step used by NBA's run_game.py (CLAUDE.md §4.1) a few
minutes before scrape_from_endpoints() runs, so the saved XHR URLs+headers
are fresh. It shares the exact on_request() classification rules this repo's
Ticketmaster scraping depends on everywhere else (inventory vs. pricing vs.
places, by URL shape), and the same navigation fix from the 2026-10-26
halftime-discard incident (domcontentloaded, 60s) -- but unlike
scrape_from_endpoints() and _goto_with_retry(), it had no coverage at all.
"""

import json
import os

import pytest

import fetch_listings


class FakeRequest:
    def __init__(self, url, headers=None):
        self.url = url
        self.headers = headers or {"x-test": "1"}


class FakePage:
    """Fakes just enough of Playwright's page for refresh_endpoints():
    on()/remove_listener() for the "request" event, and goto() which fires
    any requests handed in at construction time through the registered
    handler, simulating XHRs firing during page load."""

    def __init__(self, requests_to_fire=None, raise_on_goto=None):
        self.requests_to_fire = requests_to_fire or []
        self.raise_on_goto = raise_on_goto
        self._handlers = {}
        self.goto_calls = []
        self.wait_calls = []
        self.removed = []

    def on(self, event, handler):
        self._handlers[event] = handler

    def remove_listener(self, event, handler):
        self.removed.append((event, handler))
        self._handlers.pop(event, None)

    def goto(self, url, wait_until=None, timeout=None):
        self.goto_calls.append((url, wait_until, timeout))
        handler = self._handlers.get("request")
        if handler:
            for req in self.requests_to_fire:
                handler(req)
        if self.raise_on_goto:
            raise self.raise_on_goto

    def wait_for_timeout(self, ms):
        self.wait_calls.append(ms)


INVENTORY_URL = "https://services.ticketmaster.com/facets?by=section&eventId=123"
PRICING_URL = "https://services.ticketmaster.com/facets?by=offers&totalpricerange=1"
PLACES_URL = "https://services.ticketmaster.com/facets?compress=places&eventId=123"


def test_returns_false_and_writes_nothing_when_inventory_never_captured(tmp_path):
    page = FakePage(requests_to_fire=[])
    out_path = str(tmp_path / "sub" / "endpoints.json")

    ok = fetch_listings.refresh_endpoints(page, "https://example.com/event", out_path)

    assert ok is False
    assert not os.path.exists(out_path)


def test_captures_inventory_and_writes_endpoints_json(tmp_path):
    page = FakePage(requests_to_fire=[FakeRequest(INVENTORY_URL)])
    out_path = str(tmp_path / "sub" / "endpoints.json")

    ok = fetch_listings.refresh_endpoints(page, "https://example.com/event", out_path)

    assert ok is True
    assert os.path.exists(out_path)
    with open(out_path) as f:
        data = json.load(f)
    assert data["inventory"]["url"] == INVENTORY_URL
    assert data["inventory"]["headers"] == {"x-test": "1"}
    assert data["pricing"] is None
    assert data["places"] is None


def test_creates_missing_parent_directory(tmp_path):
    # endpoints.json's parent dir may not exist yet on a fresh checkout.
    out_path = str(tmp_path / "does" / "not" / "exist" / "endpoints.json")
    page = FakePage(requests_to_fire=[FakeRequest(INVENTORY_URL)])

    assert not os.path.isdir(os.path.dirname(out_path))
    fetch_listings.refresh_endpoints(page, "https://example.com/event", out_path)
    assert os.path.exists(out_path)


def test_inventory_pricing_and_places_classified_separately(tmp_path):
    page = FakePage(requests_to_fire=[
        FakeRequest(INVENTORY_URL, headers={"h": "inv"}),
        FakeRequest(PRICING_URL, headers={"h": "price"}),
        FakeRequest(PLACES_URL, headers={"h": "places"}),
    ])
    out_path = str(tmp_path / "endpoints.json")

    ok = fetch_listings.refresh_endpoints(page, "https://example.com/event", out_path)

    assert ok is True
    with open(out_path) as f:
        data = json.load(f)
    assert data["inventory"]["url"] == INVENTORY_URL
    assert data["pricing"]["url"] == PRICING_URL
    assert data["places"]["url"] == PLACES_URL


def test_compress_places_url_is_never_classified_as_inventory(tmp_path):
    # A places-facets URL also contains "facets", but must not be mistaken
    # for the inventory endpoint even if it happens to come first.
    places_with_section = "https://services.ticketmaster.com/facets?compress=places&section=1"
    page = FakePage(requests_to_fire=[FakeRequest(places_with_section)])
    out_path = str(tmp_path / "endpoints.json")

    ok = fetch_listings.refresh_endpoints(page, "https://example.com/event", out_path)

    # Not classified as inventory, so nothing worth saving was captured.
    assert ok is False


def test_by_offers_url_is_never_classified_as_inventory(tmp_path):
    by_offers_url = "https://services.ticketmaster.com/facets?by=offers&section=1"
    page = FakePage(requests_to_fire=[FakeRequest(by_offers_url)])
    out_path = str(tmp_path / "endpoints.json")

    ok = fetch_listings.refresh_endpoints(page, "https://example.com/event", out_path)

    assert ok is False


def test_first_matching_request_wins_for_each_bucket(tmp_path):
    first = FakeRequest(INVENTORY_URL, headers={"h": "first"})
    second = FakeRequest(INVENTORY_URL + "&dup=1", headers={"h": "second"})
    page = FakePage(requests_to_fire=[first, second])
    out_path = str(tmp_path / "endpoints.json")

    fetch_listings.refresh_endpoints(page, "https://example.com/event", out_path)

    with open(out_path) as f:
        data = json.load(f)
    assert data["inventory"]["headers"] == {"h": "first"}


def test_non_ticketmaster_requests_are_ignored(tmp_path):
    page = FakePage(requests_to_fire=[
        FakeRequest("https://cdn.example.com/analytics/facets?by=section"),
    ])
    out_path = str(tmp_path / "endpoints.json")

    ok = fetch_listings.refresh_endpoints(page, "https://example.com/event", out_path)

    assert ok is False


def test_navigates_with_domcontentloaded_and_60s_timeout(tmp_path):
    # Same navigation fix as _goto_with_retry() (2026-10-26 halftime-discard
    # incident): waiting for the full "load" event also waits on TM's
    # trackers, which don't finish on a busy Sunday.
    page = FakePage(requests_to_fire=[FakeRequest(INVENTORY_URL)])
    out_path = str(tmp_path / "endpoints.json")

    fetch_listings.refresh_endpoints(page, "https://example.com/event", out_path)

    assert page.goto_calls == [("https://example.com/event", "domcontentloaded", 60000)]


def test_request_listener_is_removed_even_when_goto_raises(tmp_path):
    page = FakePage(raise_on_goto=fetch_listings.PlaywrightTimeoutError("nav timeout"))
    out_path = str(tmp_path / "endpoints.json")

    with pytest.raises(fetch_listings.PlaywrightTimeoutError):
        fetch_listings.refresh_endpoints(page, "https://example.com/event", out_path)

    assert len(page.removed) == 1
    assert page.removed[0][0] == "request"
