"""
Tests for wnba_run_game.py's get_live_game_status().

This is the ESPN live-scoreboard poller wait_for_halftime() calls every
POLL_INTERVAL_SEC to decide when Q2 has wound down far enough to trigger the
halftime scrape. CLAUDE.md §4.2 notes ESPN's API is confirmed-blocked (HTTP
403) from the actual runner machine, so in production this always falls
through to wait_for_halftime()'s fixed-minute fallback timer -- but the
parsing logic itself (matching the right team out of the scoreboard
response, defaulting missing fields, and treating any fetch/HTTP failure as
"no live data yet") is pure and worth covering on its own, independent of
whether ESPN is actually reachable.

These tests monkeypatch wnba_run_game.req.get so nothing here makes a real
network call.
"""

import pytest

import wnba_run_game as wrg
from wnba_run_game import get_live_game_status


class _FakeResponse:
    def __init__(self, payload, status_error=None):
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        return self._payload


def _event(abbr, period=2, clock="1:30", desc="In Progress"):
    return {
        "competitions": [
            {
                "competitors": [
                    {"team": {"abbreviation": abbr}},
                ],
                "status": {
                    "period": period,
                    "displayClock": clock,
                    "type": {"description": desc},
                },
            }
        ]
    }


# ── happy path ────────────────────────────────────────────────────────────

def test_matching_team_returns_period_clock_and_description(monkeypatch):
    payload = {"events": [_event("NY", period=2, clock="1:30", desc="In Progress")]}
    monkeypatch.setattr(wrg.req, "get", lambda *a, **k: _FakeResponse(payload))

    result = get_live_game_status("NY", "2026-06-30")

    assert result == (2, pytest.approx(1.5), "In Progress")


def test_matching_team_found_in_second_event_not_first(monkeypatch):
    payload = {
        "events": [
            _event("LVA", period=1, clock="5:00", desc="In Progress"),
            _event("NY", period=2, clock="0:45", desc="In Progress"),
        ]
    }
    monkeypatch.setattr(wrg.req, "get", lambda *a, **k: _FakeResponse(payload))

    result = get_live_game_status("NY", "2026-06-30")

    assert result == (2, pytest.approx(0.75), "In Progress")


def test_matching_team_found_as_second_competitor(monkeypatch):
    event = {
        "competitions": [
            {
                "competitors": [
                    {"team": {"abbreviation": "LVA"}},
                    {"team": {"abbreviation": "NY"}},
                ],
                "status": {
                    "period": 2,
                    "displayClock": "2:00",
                    "type": {"description": "In Progress"},
                },
            }
        ]
    }
    monkeypatch.setattr(wrg.req, "get", lambda *a, **k: _FakeResponse({"events": [event]}))

    result = get_live_game_status("NY", "2026-06-30")

    assert result == (2, pytest.approx(2.0), "In Progress")


# ── not found / empty ────────────────────────────────────────────────────

def test_team_not_present_in_any_event_returns_none(monkeypatch):
    payload = {"events": [_event("LVA"), _event("SEA")]}
    monkeypatch.setattr(wrg.req, "get", lambda *a, **k: _FakeResponse(payload))

    assert get_live_game_status("NY", "2026-06-30") is None


def test_no_events_at_all_returns_none(monkeypatch):
    monkeypatch.setattr(wrg.req, "get", lambda *a, **k: _FakeResponse({"events": []}))

    assert get_live_game_status("NY", "2026-06-30") is None


def test_missing_events_key_returns_none(monkeypatch):
    monkeypatch.setattr(wrg.req, "get", lambda *a, **k: _FakeResponse({}))

    assert get_live_game_status("NY", "2026-06-30") is None


# ── failure modes are swallowed, not raised ──────────────────────────────

def test_request_exception_is_caught_and_returns_none(monkeypatch):
    def _raise(*a, **k):
        raise ConnectionError("boom")

    monkeypatch.setattr(wrg.req, "get", _raise)

    assert get_live_game_status("NY", "2026-06-30") is None


def test_non_2xx_status_is_caught_and_returns_none(monkeypatch):
    resp = _FakeResponse({"events": []}, status_error=Exception("HTTP 403"))
    monkeypatch.setattr(wrg.req, "get", lambda *a, **k: resp)

    assert get_live_game_status("NY", "2026-06-30") is None


# ── missing/default fields ───────────────────────────────────────────────

def test_missing_period_defaults_to_zero(monkeypatch):
    event = {
        "competitions": [
            {
                "competitors": [{"team": {"abbreviation": "NY"}}],
                "status": {
                    "displayClock": "5:00",
                    "type": {"description": "Pre-Game"},
                },
            }
        ]
    }
    monkeypatch.setattr(wrg.req, "get", lambda *a, **k: _FakeResponse({"events": [event]}))

    result = get_live_game_status("NY", "2026-06-30")

    assert result == (0, pytest.approx(5.0), "Pre-Game")


def test_missing_display_clock_falls_back_to_parse_clock_minutes_default(monkeypatch):
    event = {
        "competitions": [
            {
                "competitors": [{"team": {"abbreviation": "NY"}}],
                "status": {
                    "period": 2,
                    "type": {"description": "Halftime"},
                },
            }
        ]
    }
    monkeypatch.setattr(wrg.req, "get", lambda *a, **k: _FakeResponse({"events": [event]}))

    result = get_live_game_status("NY", "2026-06-30")

    # parse_clock_minutes("") == 99.0 -- see test_wnba_run_game.py's
    # coverage of parse_clock_minutes() for the fallback itself.
    assert result == (2, 99.0, "Halftime")


# ── URL construction ─────────────────────────────────────────────────────

def test_date_dashes_are_stripped_in_the_requested_url(monkeypatch):
    captured = {}

    def _fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        return _FakeResponse({"events": []})

    monkeypatch.setattr(wrg.req, "get", _fake_get)

    get_live_game_status("NY", "2026-06-30")

    assert "dates=20260630" in captured["url"]
    assert "-" not in captured["url"].split("dates=")[1]
