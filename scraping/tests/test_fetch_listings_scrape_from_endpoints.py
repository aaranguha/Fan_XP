"""
Tests for fetch_listings.scrape_from_endpoints() — the no-navigation re-fetch
path used only by NBA's halftime re-scrape (CLAUDE.md §4.1) to avoid
re-triggering TM bot detection by reloading the event page. It replays
previously captured XHR URLs/headers via page.request.get() instead.

Covers the pure data-shaping logic around that I/O boundary: building the
price map from a pricing facets response (including the price-field fallback
chain and the per-section "__sec__" minimum-price sentinel), falling back
from a saved pricing URL to one constructed from the inventory URL when the
saved one yields nothing, and tolerating a missing places/pricing endpoint.
"""

import json

import pytest

import fetch_listings


class _Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class _StubRequest:
    """Fakes Playwright's page.request — routes by exact URL or by a
    substring unique to the constructed pricing URL, since the exact string
    the code builds from urlencode()'s dict ordering isn't worth pinning."""

    def __init__(self, by_exact=None, by_contains=None):
        self.by_exact = by_exact or {}
        self.by_contains = by_contains or []
        self.calls = []

    def get(self, url, headers=None):
        self.calls.append(url)
        if url in self.by_exact:
            return _Resp(self.by_exact[url])
        for needle, data in self.by_contains:
            if needle in url:
                return _Resp(data)
        raise AssertionError(f"unexpected URL fetched: {url}")


class _StubPage:
    def __init__(self, request):
        self.request = request


INVENTORY_URL = (
    "https://services.ticketmaster.com/facets"
    "?apikey=ABC&eventId=123&compress=places&includeWithoutSeat=true&by=section"
)


def _write_endpoints(tmp_path, inventory_url=INVENTORY_URL, pricing=None, places=None):
    path = tmp_path / "endpoints.json"
    data = {"inventory": {"url": inventory_url, "headers": {"h": "1"}}}
    if pricing is not None:
        data["pricing"] = pricing
    if places is not None:
        data["places"] = places
    path.write_text(json.dumps(data))
    return str(path)


def test_missing_inventory_key_raises(tmp_path):
    path = tmp_path / "endpoints.json"
    path.write_text(json.dumps({"pricing": None, "places": None}))

    page = _StubPage(_StubRequest())
    with pytest.raises(RuntimeError):
        fetch_listings.scrape_from_endpoints(page, str(path), "2026-10-18T12:00:00Z")


def test_embedded_offers_extracted_from_inventory_response(tmp_path):
    endpoints_path = _write_endpoints(tmp_path)
    inventory_data = {
        "facets": [{"count": 2}],
        "_embedded": {"offer": [{"id": "off-1"}, {"id": "off-2"}]},
    }
    page = _StubPage(_StubRequest(by_exact={INVENTORY_URL: inventory_data}))

    all_facets, offer_price_map, places_facets, embedded_offers = (
        fetch_listings.scrape_from_endpoints(page, endpoints_path, "2026-10-18T12:00:00Z")
    )

    assert all_facets == [{"count": 2}]
    assert embedded_offers == [{"id": "off-1"}, {"id": "off-2"}]
    assert offer_price_map == {}
    assert places_facets == []


def test_saved_pricing_used_first_when_present(tmp_path):
    saved_url = "https://saved.example.com/pricing"
    endpoints_path = _write_endpoints(
        tmp_path, pricing={"url": saved_url, "headers": {}}
    )
    inventory_data = {"facets": [], "_embedded": {}}
    pricing_data = {
        "facets": [{"totalPriceRange": [{"min": 42}], "offers": ["off-1"]}]
    }
    page = _StubPage(_StubRequest(by_exact={
        INVENTORY_URL: inventory_data,
        saved_url: pricing_data,
    }))

    _, offer_price_map, _, _ = fetch_listings.scrape_from_endpoints(
        page, endpoints_path, "2026-10-18T12:00:00Z"
    )

    assert offer_price_map["off-1"] == 42
    # The constructed URL (built from the inventory URL) is never fetched
    # once the saved pricing source already produced offers.
    assert all("show=totalpricerange" not in c for c in page.request.calls)


def test_falls_back_to_constructed_pricing_url_when_saved_pricing_is_empty(tmp_path):
    saved_url = "https://saved.example.com/pricing"
    endpoints_path = _write_endpoints(
        tmp_path, pricing={"url": saved_url, "headers": {}}
    )
    inventory_data = {"facets": [], "_embedded": {}}
    empty_pricing_data = {"facets": []}  # no offers -> offer_price_map stays empty
    constructed_pricing_data = {
        "facets": [{"priceRange": [{"low": 77}], "offers": ["off-9"]}]
    }
    page = _StubPage(_StubRequest(
        by_exact={INVENTORY_URL: inventory_data, saved_url: empty_pricing_data},
        by_contains=[("show=totalpricerange", constructed_pricing_data)],
    ))

    _, offer_price_map, _, _ = fetch_listings.scrape_from_endpoints(
        page, endpoints_path, "2026-10-18T12:00:00Z"
    )

    assert offer_price_map["off-9"] == 77


def test_constructed_pricing_url_strips_removed_params_and_keeps_the_rest(tmp_path):
    endpoints_path = _write_endpoints(tmp_path)  # no saved pricing -> must construct
    inventory_data = {"facets": [], "_embedded": {}}
    constructed_pricing_data = {"facets": []}
    page = _StubPage(_StubRequest(
        by_exact={INVENTORY_URL: inventory_data},
        by_contains=[("show=totalpricerange", constructed_pricing_data)],
    ))

    fetch_listings.scrape_from_endpoints(page, endpoints_path, "2026-10-18T12:00:00Z")

    constructed_calls = [c for c in page.request.calls if "show=totalpricerange" in c]
    assert len(constructed_calls) == 1
    constructed_url = constructed_calls[0]
    assert "by=offers" in constructed_url
    assert "apikey=ABC" in constructed_url and "eventId=123" in constructed_url
    assert "compress=" not in constructed_url
    assert "includewithoutseat" not in constructed_url.lower()


def test_price_fallback_chain_prefers_totalpricerange_then_pricerange_then_listprice(tmp_path):
    saved_url = "https://saved.example.com/pricing"
    endpoints_path = _write_endpoints(tmp_path, pricing={"url": saved_url, "headers": {}})
    inventory_data = {"facets": [], "_embedded": {}}
    pricing_data = {
        "facets": [
            {"totalPriceRange": [{"min": 10}], "priceRange": [{"low": 999}], "offers": ["a"]},
            {"priceRange": [{"low": 20}], "listPrice": 999, "offers": ["b"]},
            {"listPrice": 30, "offers": ["c"]},
            {"price": 40, "offers": ["d"]},
        ]
    }
    page = _StubPage(_StubRequest(by_exact={INVENTORY_URL: inventory_data, saved_url: pricing_data}))

    _, offer_price_map, _, _ = fetch_listings.scrape_from_endpoints(
        page, endpoints_path, "2026-10-18T12:00:00Z"
    )

    assert offer_price_map["a"] == 10
    assert offer_price_map["b"] == 20
    assert offer_price_map["c"] == 30
    assert offer_price_map["d"] == 40


def test_section_sentinel_keeps_the_lower_price_across_duplicate_sections(tmp_path):
    saved_url = "https://saved.example.com/pricing"
    endpoints_path = _write_endpoints(tmp_path, pricing={"url": saved_url, "headers": {}})
    inventory_data = {"facets": [], "_embedded": {}}
    pricing_data = {
        "facets": [
            {"totalPriceRange": [{"min": 150}], "offers": ["a"], "section": "115"},
            {"totalPriceRange": [{"min": 60}], "offers": ["b"], "section": "115"},
        ]
    }
    page = _StubPage(_StubRequest(by_exact={INVENTORY_URL: inventory_data, saved_url: pricing_data}))

    _, offer_price_map, _, _ = fetch_listings.scrape_from_endpoints(
        page, endpoints_path, "2026-10-18T12:00:00Z"
    )

    assert offer_price_map["__sec__115"] == 60


def test_missing_places_endpoint_returns_empty_list_without_crashing(tmp_path):
    endpoints_path = _write_endpoints(tmp_path)  # no "places" key at all
    inventory_data = {"facets": [], "_embedded": {}}
    page = _StubPage(_StubRequest(by_exact={INVENTORY_URL: inventory_data}))

    _, _, places_facets, _ = fetch_listings.scrape_from_endpoints(
        page, endpoints_path, "2026-10-18T12:00:00Z"
    )

    assert places_facets == []


def test_places_facets_returned_when_endpoint_present(tmp_path):
    places_url = "https://services.ticketmaster.com/places-facets"
    endpoints_path = _write_endpoints(tmp_path, places={"url": places_url, "headers": {}})
    inventory_data = {"facets": [], "_embedded": {}}
    places_data = {"facets": [{"section": "115", "row": "A", "count": 1}]}
    page = _StubPage(_StubRequest(by_exact={INVENTORY_URL: inventory_data, places_url: places_data}))

    _, _, places_facets, _ = fetch_listings.scrape_from_endpoints(
        page, endpoints_path, "2026-10-18T12:00:00Z"
    )

    assert places_facets == [{"section": "115", "row": "A", "count": 1}]
