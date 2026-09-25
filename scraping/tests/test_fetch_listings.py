"""
Tests for fetch_listings.find_next_home_game()'s event choice: TM lists paid
add-ons (club passes, hotel packages) as separate same-day events under the
team's keyword, and picking one scrapes a page with no seat inventory.
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


def test_club_pass_listed_first_is_skipped(monkeypatch):
    _stub_tm(monkeypatch, [
        _event("Pittsburgh Steelers v Bengals: 1933 Club Pass (NOT A GAME TICKET)"),
        _event("Pittsburgh Steelers vs. Cincinnati Bengals"),
    ])

    e = fetch_listings.find_next_home_game("Pittsburgh Steelers", "2026-09-27", "Football")

    assert e["name"] == "Pittsburgh Steelers vs. Cincinnati Bengals"


def test_hotel_package_is_skipped(monkeypatch):
    _stub_tm(monkeypatch, [
        _event("Las Vegas Raiders vs. Miami Dolphins | Official Hotel Packages"),
        _event("Las Vegas Raiders vs. Miami Dolphins"),
    ])

    e = fetch_listings.find_next_home_game("Las Vegas Raiders", "2026-09-27", "Football")

    assert e["name"] == "Las Vegas Raiders vs. Miami Dolphins"


def test_only_add_ons_raises_instead_of_scraping_one(monkeypatch):
    _stub_tm(monkeypatch, [
        _event("Pittsburgh Steelers v Bengals: 1933 Club Pass (NOT A GAME TICKET)"),
    ])

    with pytest.raises(RuntimeError):
        fetch_listings.find_next_home_game("Pittsburgh Steelers", "2026-09-27", "Football")
