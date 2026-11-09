"""
Tests for scripts/smoke_check.py's check_browser() — the deploy-guard's
headless-browser page-error check, which previously had no test coverage
(test_smoke_check.py explicitly notes it was left out there because exercising
its *real* path means launching an actual Chromium).

These tests never launch a real browser: check_browser() does
`from playwright.sync_api import sync_playwright` *inside the function body*,
so a fake module injected into sys.modules["playwright.sync_api"] is picked
up on every call. That lets us exercise the pure control-flow/error-handling
logic — the ImportError skip path, per-page error isolation, the "navigation
failed" message, the 600-char truncation of joined errors, and the
launch-failure-is-an-environment-problem-not-a-broken-deploy distinction —
without ever touching Chromium.
"""
import sys
import types

import pytest

import smoke_check


class FakePage:
    def __init__(self, fire_errors=None, goto_exception=None):
        self._fire_errors = fire_errors or []
        self._goto_exception = goto_exception
        self._pageerror_handler = None
        self.closed = False

    def on(self, event, handler):
        if event == "pageerror":
            self._pageerror_handler = handler

    def goto(self, url, wait_until=None, timeout=None):
        if self._goto_exception is not None:
            raise self._goto_exception
        for err in self._fire_errors:
            self._pageerror_handler(err)

    def wait_for_timeout(self, ms):
        pass

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, pages):
        self._pages = list(pages)
        self.closed = False

    def new_page(self):
        return self._pages.pop(0)

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, browser=None, launch_exception=None):
        self._browser = browser
        self._launch_exception = launch_exception

    def launch(self):
        if self._launch_exception is not None:
            raise self._launch_exception
        return self._browser


class FakeSyncPlaywright:
    def __init__(self, chromium):
        self.chromium = chromium

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _install_fake_playwright(monkeypatch, chromium):
    fake_module = types.ModuleType("playwright.sync_api")
    fake_module.sync_playwright = lambda: FakeSyncPlaywright(chromium)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)


# ── ImportError (playwright not installed) ──────────────────────────────────

def test_playwright_not_installed_skips_without_recording_a_failure(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    failures = []
    smoke_check.check_browser("https://site.example", failures)

    assert failures == []
    assert "skip browser checks (playwright not installed)" in capsys.readouterr().out


# ── Happy path ────────────────────────────────────────────────────────────────

def test_all_pages_load_with_no_page_errors_records_no_failures(monkeypatch, capsys):
    pages = [FakePage() for _ in smoke_check.BROWSER_PAGES]
    _install_fake_playwright(monkeypatch, FakeChromium(browser=FakeBrowser(pages)))

    failures = []
    smoke_check.check_browser("https://site.example", failures)

    assert failures == []
    out = capsys.readouterr().out
    for path in smoke_check.BROWSER_PAGES:
        assert "ok   browser %s (no page errors)" % path in out
    assert all(p.closed for p in pages)


# ── pageerror events ─────────────────────────────────────────────────────────

def test_pageerror_on_one_page_is_recorded_without_affecting_the_other(monkeypatch):
    assert len(smoke_check.BROWSER_PAGES) == 2
    bad_page = FakePage(fire_errors=["TypeError: x is not a function"])
    good_page = FakePage()
    _install_fake_playwright(monkeypatch, FakeChromium(browser=FakeBrowser([bad_page, good_page])))

    failures = []
    smoke_check.check_browser("https://site.example", failures)

    assert len(failures) == 1
    assert smoke_check.BROWSER_PAGES[0] in failures[0]
    assert "uncaught page errors" in failures[0]
    assert "TypeError: x is not a function" in failures[0]
    assert smoke_check.BROWSER_PAGES[1] not in failures[0]


def test_multiple_pageerrors_on_one_page_are_joined_with_semicolons(monkeypatch):
    page = FakePage(fire_errors=["first error", "second error"])
    other = FakePage()
    _install_fake_playwright(monkeypatch, FakeChromium(browser=FakeBrowser([page, other])))

    failures = []
    smoke_check.check_browser("https://site.example", failures)

    assert len(failures) == 1
    assert "first error; second error" in failures[0]


def test_long_joined_errors_are_truncated_to_600_chars(monkeypatch):
    long_error = "x" * 1000
    page = FakePage(fire_errors=[long_error])
    other = FakePage()
    _install_fake_playwright(monkeypatch, FakeChromium(browser=FakeBrowser([page, other])))

    failures = []
    smoke_check.check_browser("https://site.example", failures)

    assert len(failures) == 1
    # "BROWSER <path>: uncaught page errors: " prefix + at most 600 chars of joined errors.
    prefix = "BROWSER %s: uncaught page errors: " % smoke_check.BROWSER_PAGES[0]
    assert failures[0].startswith(prefix)
    assert len(failures[0]) - len(prefix) == 600


# ── goto() navigation failure ────────────────────────────────────────────────

def test_goto_exception_is_recorded_as_navigation_failed(monkeypatch):
    page = FakePage(goto_exception=TimeoutError("Timeout 60000ms exceeded"))
    other = FakePage()
    _install_fake_playwright(monkeypatch, FakeChromium(browser=FakeBrowser([page, other])))

    failures = []
    smoke_check.check_browser("https://site.example", failures)

    assert len(failures) == 1
    assert "navigation failed" in failures[0]
    assert "Timeout 60000ms exceeded" in failures[0]


# ── Chromium itself fails to launch ──────────────────────────────────────────

def test_browser_launch_failure_skips_without_recording_a_failure(monkeypatch, capsys):
    _install_fake_playwright(
        monkeypatch,
        FakeChromium(launch_exception=RuntimeError("Executable doesn't exist")),
    )

    failures = []
    smoke_check.check_browser("https://site.example", failures)

    assert failures == []
    out = capsys.readouterr().out
    assert "skip browser checks (could not run Chromium" in out
    assert "Executable doesn't exist" in out
