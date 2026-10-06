"""
Tests for fetch_listings._goto_with_retry() -- the randomized-backoff retry
wrapper around page.goto() added in PR #45 after contending teams' halftime
re-navigations kept colliding on a fixed 5s backoff (2026-09-27, 8 teams;
recurred 2026-10-04 with 7 teams on the same fixed backoff). Backoff is now
random.randint(5000, 20000) per retry instead of a fixed value, so
simultaneously-retrying teams spread apart instead of staying in lockstep.
"""

import random

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import fetch_listings


class FakePage:
    """page.goto() fails `fail_count` times, then succeeds (or never does)."""

    def __init__(self, fail_count):
        self.fail_count = fail_count
        self.goto_calls = 0
        self.wait_calls = []

    def goto(self, url, wait_until="load", timeout=45000):
        self.goto_calls += 1
        if self.goto_calls <= self.fail_count:
            raise PlaywrightTimeoutError("navigation timeout")
        return None

    def wait_for_timeout(self, ms):
        self.wait_calls.append(ms)


def test_succeeds_immediately_with_no_retry_needed():
    page = FakePage(fail_count=0)

    fetch_listings._goto_with_retry(page, "https://example.com")

    assert page.goto_calls == 1
    assert page.wait_calls == []


def test_recovers_after_timeouts_within_retry_budget():
    page = FakePage(fail_count=2)

    fetch_listings._goto_with_retry(page, "https://example.com", retries=3)

    # 2 failed attempts + 1 successful attempt = 3 total goto() calls.
    assert page.goto_calls == 3
    # A backoff happens before each retry, not before the final success.
    assert len(page.wait_calls) == 2


def test_raises_after_exhausting_all_retries():
    page = FakePage(fail_count=100)

    with pytest.raises(PlaywrightTimeoutError):
        fetch_listings._goto_with_retry(page, "https://example.com", retries=3)

    # Initial attempt + 3 retries = 4 total goto() calls, then it gives up.
    assert page.goto_calls == 4
    # A backoff is attempted before each of the 3 retries, never after the
    # final (4th) failure -- it raises instead of sleeping again.
    assert len(page.wait_calls) == 3


def test_backoff_is_randomized_within_documented_window(monkeypatch):
    """
    Regression test for the actual bug this PR fixed: a FIXED backoff let
    contending teams retry in lockstep and keep re-colliding. Assert the
    function draws from random.randint(5000, 20000) rather than using any
    fixed constant (e.g. the old 5000ms).
    """
    seen = []

    def fake_randint(lo, hi):
        assert (lo, hi) == (5000, 20000)
        val = 12345 + len(seen)  # a different value each call
        seen.append(val)
        return val

    monkeypatch.setattr(random, "randint", fake_randint)

    page = FakePage(fail_count=3)
    fetch_listings._goto_with_retry(page, "https://example.com", retries=3)

    assert page.wait_calls == seen
    assert len(set(page.wait_calls)) == len(page.wait_calls)  # none repeated


def test_default_retries_is_three():
    """
    The retry budget was raised from 2 to 3 in PR #45 for headroom on a full
    13-game Sunday slate -- pin the default so a future change is deliberate.
    """
    page = FakePage(fail_count=100)

    with pytest.raises(PlaywrightTimeoutError):
        fetch_listings._goto_with_retry(page, "https://example.com")

    assert page.goto_calls == 4  # 1 initial + default 3 retries


def test_non_timeout_exceptions_are_not_retried():
    """Only a bare navigation timeout is retried; any other error propagates
    immediately, with no backoff and no further goto() attempts."""

    class ExplodingPage:
        def __init__(self):
            self.goto_calls = 0
            self.wait_calls = []

        def goto(self, url, wait_until="load", timeout=45000):
            self.goto_calls += 1
            raise ValueError("boom")

        def wait_for_timeout(self, ms):
            self.wait_calls.append(ms)

    page = ExplodingPage()

    with pytest.raises(ValueError):
        fetch_listings._goto_with_retry(page, "https://example.com")

    assert page.goto_calls == 1
    assert page.wait_calls == []
