"""
Tests for scripts/smoke_check.py -- the deploy-guard's live smoke check
against the deployed Render API and Vercel site (see
.github/workflows/deploy-guard.yml). A false "pass" here would move the
`last-good` tag onto a broken deploy; a false "fail" would revert a good
push and open a spurious issue -- so the retry/skip/deadline decisions in
check_healthz()/check_deep_health()/check_api()/check_site() are worth
covering directly.

This is pure decision logic with the real network calls isolated behind
fetch()/get_json(), so every test here monkeypatches those two functions
(no real HTTP) plus a fake clock (so check_healthz's real ~10-minute
deadline and the various sleep()-based retry loops run their real number
of iterations without the test suite actually waiting).
"""
import os
import sys

import pytest

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts",
    ),
)

import smoke_check


@pytest.fixture(autouse=True)
def fake_clock(monkeypatch):
    state = {"now": 1_000_000.0}

    def fake_time():
        return state["now"]

    def fake_sleep(seconds):
        state["now"] += seconds

    monkeypatch.setattr(smoke_check.time, "time", fake_time)
    monkeypatch.setattr(smoke_check.time, "sleep", fake_sleep)
    return state


# ── get_json() ────────────────────────────────────────────────────────────

def test_get_json_parses_a_valid_json_body(monkeypatch):
    monkeypatch.setattr(smoke_check, "fetch", lambda url, timeout: (200, '{"ok": true}'))

    status, data = smoke_check.get_json("https://api.example.com/x")

    assert status == 200
    assert data == {"ok": True}


def test_get_json_returns_none_data_on_invalid_json_without_raising(monkeypatch):
    # An HTML error page (e.g. Render's own 502 page) is not JSON; callers
    # must get a clean None, not an uncaught ValueError.
    monkeypatch.setattr(smoke_check, "fetch", lambda url, timeout: (502, "<html>Bad Gateway</html>"))

    status, data = smoke_check.get_json("https://api.example.com/x")

    assert status == 502
    assert data is None


# ── check_site() ──────────────────────────────────────────────────────────

def test_check_site_passes_when_every_page_matches_on_first_try(monkeypatch):
    body = "Seat Intelligence Steelers gamepick fanxp-auth.js --bg id=\"signed-in\""
    monkeypatch.setattr(smoke_check, "fetch", lambda url, timeout: (200, body))
    failures = []

    smoke_check.check_site("https://fan-xp.vercel.app", failures)

    assert failures == []


def test_check_site_retries_transient_failures_before_succeeding(monkeypatch):
    calls = {"n": 0}
    body = "Seat Intelligence Steelers gamepick fanxp-auth.js --bg id=\"signed-in\""

    def fake_fetch(url, timeout):
        calls["n"] += 1
        if calls["n"] % 3 != 0:
            return 0, "ConnectionError: reset"
        return 200, body

    monkeypatch.setattr(smoke_check, "fetch", fake_fetch)
    failures = []

    smoke_check.check_site("https://fan-xp.vercel.app", failures)

    assert failures == []


def test_check_site_reports_http_failure_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(smoke_check, "fetch", lambda url, timeout: (500, "Internal Server Error"))
    failures = []

    smoke_check.check_site("https://fan-xp.vercel.app", failures)

    assert len(failures) == len(smoke_check.SITE_PAGES)
    assert all("HTTP 500" in f for f in failures)


def test_check_site_distinguishes_missing_marker_from_http_failure(monkeypatch):
    # A 200 whose body doesn't contain the expected marker is a different
    # kind of broken deploy (wrong content served) than a non-200 status --
    # the failure message must say which one happened.
    monkeypatch.setattr(smoke_check, "fetch", lambda url, timeout: (200, "a generic placeholder page"))
    failures = []

    smoke_check.check_site("https://fan-xp.vercel.app", failures)

    assert len(failures) == len(smoke_check.SITE_PAGES)
    assert all("200 but marker" in f for f in failures)


# ── check_healthz() ───────────────────────────────────────────────────────

def test_check_healthz_skips_on_404_when_commit_not_expected_and_not_required(monkeypatch):
    monkeypatch.setattr(smoke_check, "get_json", lambda url, timeout=smoke_check.API_TIMEOUT: (404, None))
    failures = []

    smoke_check.check_healthz("https://api.example.com", expect_commit="", require=False, failures=failures)

    assert failures == []


def test_check_healthz_succeeds_immediately_when_ok_and_no_commit_expected(monkeypatch):
    monkeypatch.setattr(
        smoke_check, "get_json",
        lambda url, timeout=smoke_check.API_TIMEOUT: (200, {"ok": True, "commit": "abc123"}),
    )
    failures = []

    smoke_check.check_healthz("https://api.example.com", expect_commit="", require=False, failures=failures)

    assert failures == []


def test_check_healthz_polls_until_the_expected_commit_shows_up(monkeypatch):
    calls = {"n": 0}

    def fake_get_json(url, timeout=smoke_check.API_TIMEOUT):
        calls["n"] += 1
        commit = "old_sha" if calls["n"] < 3 else "new_sha"
        return 200, {"ok": True, "commit": commit}

    monkeypatch.setattr(smoke_check, "get_json", fake_get_json)
    failures = []

    smoke_check.check_healthz("https://api.example.com", expect_commit="new_sha", require=False, failures=failures)

    assert failures == []
    assert calls["n"] == 3


def test_check_healthz_fails_after_the_deadline_if_commit_never_matches(monkeypatch):
    monkeypatch.setattr(
        smoke_check, "get_json",
        lambda url, timeout=smoke_check.API_TIMEOUT: (200, {"ok": True, "commit": "stale_sha"}),
    )
    failures = []

    smoke_check.check_healthz("https://api.example.com", expect_commit="new_sha", require=False, failures=failures)

    assert len(failures) == 1
    assert "gave up after 10 min" in failures[0]
    assert "stale_sha" in failures[0]


def test_check_healthz_404_is_strict_once_a_commit_is_expected(monkeypatch):
    # Without expect_commit, a 404 is tolerated (endpoint not deployed /
    # not applicable yet). Once a specific commit is expected, the same
    # 404 must NOT be silently skipped -- that would let a dead API pass
    # the guard just because /healthz happens to 404.
    monkeypatch.setattr(smoke_check, "get_json", lambda url, timeout=smoke_check.API_TIMEOUT: (404, None))
    failures = []

    smoke_check.check_healthz("https://api.example.com", expect_commit="new_sha", require=False, failures=failures)

    assert len(failures) == 1
    assert "gave up after 10 min" in failures[0]


def test_check_healthz_404_is_strict_when_require_flag_is_set_even_without_a_commit(monkeypatch):
    monkeypatch.setattr(smoke_check, "get_json", lambda url, timeout=smoke_check.API_TIMEOUT: (404, None))
    failures = []

    smoke_check.check_healthz("https://api.example.com", expect_commit="", require=True, failures=failures)

    assert len(failures) == 1
    assert "gave up after 10 min" in failures[0]


# ── check_deep_health() ───────────────────────────────────────────────────

def test_check_deep_health_skips_on_404_when_not_required(monkeypatch):
    monkeypatch.setattr(smoke_check, "get_json", lambda url, timeout=smoke_check.API_TIMEOUT: (404, None))
    failures = []

    smoke_check.check_deep_health("https://api.example.com", require=False, failures=failures)

    assert failures == []


def test_check_deep_health_succeeds_on_a_later_attempt(monkeypatch):
    calls = {"n": 0}

    def fake_get_json(url, timeout=smoke_check.API_TIMEOUT):
        calls["n"] += 1
        if calls["n"] < 3:
            return 503, {"ok": False}
        return 200, {"ok": True, "checks": {"supabase": "ok"}}

    monkeypatch.setattr(smoke_check, "get_json", fake_get_json)
    failures = []

    smoke_check.check_deep_health("https://api.example.com", require=False, failures=failures)

    assert failures == []
    assert calls["n"] == 3


def test_check_deep_health_fails_once_all_four_attempts_are_exhausted(monkeypatch):
    # 503 here means the database is unreachable (a paused Supabase project
    # -- see CLAUDE.md), not a code bug, but the guard still has to report
    # it rather than wave it through.
    calls = {"n": 0}

    def fake_get_json(url, timeout=smoke_check.API_TIMEOUT):
        calls["n"] += 1
        return 503, {"ok": False, "checks": {"supabase": "down"}}

    monkeypatch.setattr(smoke_check, "get_json", fake_get_json)
    failures = []

    smoke_check.check_deep_health("https://api.example.com", require=False, failures=failures)

    assert calls["n"] == 4
    assert len(failures) == 1
    assert "HTTP 503" in failures[0]


# ── check_api() ───────────────────────────────────────────────────────────

def test_check_api_reports_each_broken_endpoint_independently(monkeypatch):
    def fake_get_json(url, timeout=smoke_check.API_TIMEOUT):
        if url.endswith("/healthz"):
            return 200, {"ok": True}
        if url.endswith("/healthz?deep=1"):
            return 200, {"ok": True, "checks": {}}
        if url.endswith("/api/nfl/steelers/games"):
            return 200, {"not": "a list"}
        if url.endswith("/api/auth/providers"):
            return 200, {"google": True}
        raise AssertionError("unexpected url: %s" % url)

    monkeypatch.setattr(smoke_check, "get_json", fake_get_json)
    failures = []

    smoke_check.check_api("https://api.example.com", expect_commit="", require_healthz=False, failures=failures)

    assert len(failures) == 2
    assert any("games" in f and "expected a JSON list" in f for f in failures)
    assert any("auth/providers" in f and "'email'" in f for f in failures)


def test_check_api_all_green_reports_no_failures(monkeypatch):
    def fake_get_json(url, timeout=smoke_check.API_TIMEOUT):
        if url.endswith("/healthz"):
            return 200, {"ok": True}
        if url.endswith("/healthz?deep=1"):
            return 200, {"ok": True, "checks": {}}
        if url.endswith("/api/nfl/steelers/games"):
            return 200, [{"id": 1}, {"id": 2}]
        if url.endswith("/api/auth/providers"):
            return 200, {"email": True}
        raise AssertionError("unexpected url: %s" % url)

    monkeypatch.setattr(smoke_check, "get_json", fake_get_json)
    failures = []

    smoke_check.check_api("https://api.example.com", expect_commit="", require_healthz=False, failures=failures)

    assert failures == []


def test_check_api_passes_the_strict_flag_through_to_deep_health(monkeypatch):
    # check_deep_health's own `require` is derived as
    # bool(expect_commit) or require_healthz -- an expected commit alone
    # must make a 404 from /healthz?deep=1 strict too, not just /healthz.
    monkeypatch.setattr(smoke_check, "get_json", lambda url, timeout=smoke_check.API_TIMEOUT: (
        (200, {"ok": True, "commit": "new_sha"}) if url.endswith("/healthz")
        else (404, None) if url.endswith("/healthz?deep=1")
        else (200, [{"id": 1}]) if url.endswith("/api/nfl/steelers/games")
        else (200, {"email": True})
    ))
    failures = []

    smoke_check.check_api("https://api.example.com", expect_commit="new_sha", require_healthz=False, failures=failures)

    assert len(failures) == 1
    assert "healthz?deep=1" in failures[0]


# ── main() ────────────────────────────────────────────────────────────────

def _patch_all_checks_ok(monkeypatch, calls=None):
    monkeypatch.setattr(smoke_check, "check_site", lambda site, failures: (calls or []).append("site"))
    monkeypatch.setattr(smoke_check, "check_browser", lambda site, failures: (calls or []).append("browser"))
    monkeypatch.setattr(smoke_check, "check_api", lambda *a, **k: (calls or []).append("api"))


def test_main_exits_zero_when_every_check_reports_no_failures(monkeypatch):
    _patch_all_checks_ok(monkeypatch)
    monkeypatch.setattr(sys, "argv", [
        "smoke_check.py", "--site", "https://fan-xp.vercel.app", "--api", "https://fanxp-api.onrender.com",
    ])

    assert smoke_check.main() == 0


def test_main_exits_one_when_a_check_appends_a_failure(monkeypatch):
    def fake_check_api(api, expect_commit, require_healthz, failures):
        failures.append("API down")

    monkeypatch.setattr(smoke_check, "check_site", lambda site, failures: None)
    monkeypatch.setattr(smoke_check, "check_browser", lambda site, failures: None)
    monkeypatch.setattr(smoke_check, "check_api", fake_check_api)
    monkeypatch.setattr(sys, "argv", [
        "smoke_check.py", "--site", "https://fan-xp.vercel.app", "--api", "https://fanxp-api.onrender.com",
    ])

    assert smoke_check.main() == 1


def test_main_skip_site_and_skip_api_flags_short_circuit_their_checks(monkeypatch):
    calls = []
    monkeypatch.setattr(smoke_check, "check_site", lambda site, failures: calls.append("site"))
    monkeypatch.setattr(smoke_check, "check_browser", lambda site, failures: calls.append("browser"))
    monkeypatch.setattr(smoke_check, "check_api", lambda *a, **k: calls.append("api"))
    monkeypatch.setattr(sys, "argv", [
        "smoke_check.py", "--site", "https://fan-xp.vercel.app", "--api", "https://fanxp-api.onrender.com",
        "--skip-site", "--skip-api",
    ])

    assert smoke_check.main() == 0
    assert calls == []


def test_main_strips_trailing_slash_from_site_and_api_urls(monkeypatch):
    seen = {}

    def fake_check_site(site, failures):
        seen["site"] = site

    def fake_check_api(api, expect_commit, require_healthz, failures):
        seen["api"] = api

    monkeypatch.setattr(smoke_check, "check_site", fake_check_site)
    monkeypatch.setattr(smoke_check, "check_browser", lambda site, failures: None)
    monkeypatch.setattr(smoke_check, "check_api", fake_check_api)
    monkeypatch.setattr(sys, "argv", [
        "smoke_check.py", "--site", "https://fan-xp.vercel.app/", "--api", "https://fanxp-api.onrender.com/",
    ])

    smoke_check.main()

    assert seen["site"] == "https://fan-xp.vercel.app"
    assert seen["api"] == "https://fanxp-api.onrender.com"
