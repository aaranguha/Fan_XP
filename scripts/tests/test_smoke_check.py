"""
Tests for scripts/smoke_check.py — the live deploy-guard smoke check that
decides whether a push to main gets rolled back (.github/workflows/
deploy-guard.yml). Pure decision/retry/aggregation logic, tested here with
urllib and time mocked out so nothing hits the network or sleeps for real.

Coverage picked for this run: fetch()/get_json()'s three outcome shapes,
check_site()'s retry-then-fail behavior, check_healthz()'s strict-vs-lenient
404 handling and its poll-until-deadline loop (the actual mechanism that
decides whether deploy-guard waits, passes, or times out and triggers a
revert), check_deep_health()'s bounded retry, check_api()'s failure
aggregation across its four sub-checks, and main()'s exit-code mapping.
check_browser() is intentionally left uncovered here — exercising its real
path means launching an actual headless browser, which isn't a pure-logic
test and isn't needed to protect the decision logic above.
"""
import urllib.error

import pytest

import smoke_check


# ── fetch() ──────────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_returns_status_and_decoded_body_on_success(monkeypatch):
    monkeypatch.setattr(
        smoke_check.urllib.request, "urlopen",
        lambda req, timeout: _FakeResponse(200, "hello world"),
    )
    status, body = smoke_check.fetch("http://x/ok", 5)
    assert (status, body) == (200, "hello world")


def test_fetch_returns_http_error_code_and_body(monkeypatch):
    # urllib.error.HTTPError.read() needs a file-like fp; patch read directly.
    def fake_urlopen(req, timeout):
        err = urllib.error.HTTPError("http://x/missing", 503, "Unavailable", {}, None)
        err.read = lambda: b"service unavailable"
        raise err
    monkeypatch.setattr(smoke_check.urllib.request, "urlopen", fake_urlopen)

    status, body = smoke_check.fetch("http://x/missing", 5)

    assert status == 503
    assert body == "service unavailable"


def test_fetch_returns_zero_on_connection_failure(monkeypatch):
    def fake_urlopen(req, timeout):
        raise TimeoutError("timed out")
    monkeypatch.setattr(smoke_check.urllib.request, "urlopen", fake_urlopen)

    status, body = smoke_check.fetch("http://x/unreachable", 5)

    assert status == 0
    assert "TimeoutError" in body


# ── get_json() ───────────────────────────────────────────────────────────────

def test_get_json_parses_valid_json_body(monkeypatch):
    monkeypatch.setattr(smoke_check, "fetch", lambda url, timeout: (200, '{"ok": true}'))
    status, data = smoke_check.get_json("http://x/healthz")
    assert status == 200
    assert data == {"ok": True}


def test_get_json_returns_none_for_non_json_body(monkeypatch):
    monkeypatch.setattr(smoke_check, "fetch", lambda url, timeout: (200, "<html>not json</html>"))
    status, data = smoke_check.get_json("http://x/healthz")
    assert status == 200
    assert data is None


# ── check_site() ─────────────────────────────────────────────────────────────

def test_check_site_passes_when_every_page_has_its_marker(monkeypatch):
    body = '...Seat Intelligence...Steelers...gamepick...--bg...fanxp-auth.js...id="signed-in"...'
    monkeypatch.setattr(smoke_check, "fetch", lambda url, timeout: (200, body))
    failures = []

    smoke_check.check_site("https://fan-xp.vercel.app", failures)

    assert failures == []


def test_check_site_records_http_failure_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(smoke_check, "fetch", lambda url, timeout: (500, "server error"))
    monkeypatch.setattr(smoke_check.time, "sleep", lambda s: None)
    failures = []

    smoke_check.check_site("https://fan-xp.vercel.app", failures)

    assert len(failures) == len(smoke_check.SITE_PAGES)
    assert all("HTTP 500" in f for f in failures)


def test_check_site_records_failure_when_marker_missing_despite_200(monkeypatch):
    monkeypatch.setattr(smoke_check, "fetch", lambda url, timeout: (200, "totally unrelated body"))
    monkeypatch.setattr(smoke_check.time, "sleep", lambda s: None)
    failures = []

    smoke_check.check_site("https://fan-xp.vercel.app", failures)

    assert len(failures) == len(smoke_check.SITE_PAGES)
    assert all("200 but marker" in f for f in failures)


def test_check_site_succeeds_after_a_transient_failure_then_retry(monkeypatch):
    # First attempt for each page fails, second succeeds -- Vercel lagging
    # behind the deployment status is exactly the scenario the retry loop
    # (SITE_PAGES x 3 attempts, sleep(5) between) exists for.
    calls = {"n": 0}

    def flaky_fetch(url, timeout):
        calls["n"] += 1
        if calls["n"] <= 1:
            return 404, "not deployed yet"
        return 200, "Seat Intelligence Steelers gamepick --bg fanxp-auth.js id=\"signed-in\""

    monkeypatch.setattr(smoke_check, "fetch", flaky_fetch)
    monkeypatch.setattr(smoke_check.time, "sleep", lambda s: None)
    failures = []

    smoke_check.check_site("https://fan-xp.vercel.app", failures)

    assert failures == []


# ── check_healthz() ──────────────────────────────────────────────────────────

def test_check_healthz_skips_when_404_and_not_strict(monkeypatch):
    monkeypatch.setattr(smoke_check, "get_json", lambda url, timeout=90: (404, None))
    failures = []

    smoke_check.check_healthz("https://api", expect_commit="", require=False, failures=failures)

    assert failures == []


def test_check_healthz_fails_eventually_when_404_and_required(monkeypatch):
    times = iter([0, 1000])  # deadline=600, then immediately past it
    monkeypatch.setattr(smoke_check.time, "time", lambda: next(times))
    monkeypatch.setattr(smoke_check, "get_json", lambda url, timeout=90: (404, None))
    failures = []

    smoke_check.check_healthz("https://api", expect_commit="", require=True, failures=failures)

    assert len(failures) == 1
    assert "healthz" in failures[0]


def test_check_healthz_ok_when_commit_matches_immediately(monkeypatch):
    monkeypatch.setattr(smoke_check.time, "time", lambda: 0)
    monkeypatch.setattr(
        smoke_check, "get_json",
        lambda url, timeout=90: (200, {"ok": True, "commit": "abc123"}),
    )
    failures = []

    smoke_check.check_healthz("https://api", expect_commit="abc123", require=False, failures=failures)

    assert failures == []


def test_check_healthz_waits_for_the_right_commit_then_succeeds(monkeypatch):
    responses = iter([
        (200, {"ok": True, "commit": "stale"}),
        (200, {"ok": True, "commit": "fresh"}),
    ])
    times = iter([0, 100])  # second time.time() check: still well under deadline
    monkeypatch.setattr(smoke_check.time, "time", lambda: next(times))
    monkeypatch.setattr(smoke_check.time, "sleep", lambda s: None)
    monkeypatch.setattr(smoke_check, "get_json", lambda url, timeout=90: next(responses))
    failures = []

    smoke_check.check_healthz("https://api", expect_commit="fresh", require=False, failures=failures)

    assert failures == []


def test_check_healthz_times_out_when_commit_never_matches(monkeypatch):
    times = iter([0, 1000])  # deadline=600, then past it on the first poll
    monkeypatch.setattr(smoke_check.time, "time", lambda: next(times))
    monkeypatch.setattr(
        smoke_check, "get_json",
        lambda url, timeout=90: (200, {"ok": True, "commit": "stale"}),
    )
    failures = []

    smoke_check.check_healthz("https://api", expect_commit="fresh", require=False, failures=failures)

    assert len(failures) == 1
    assert "gave up after 10 min" in failures[0]
    assert "stale" in failures[0] and "fresh" in failures[0]


# ── check_deep_health() ──────────────────────────────────────────────────────

def test_check_deep_health_skips_when_404_and_not_required(monkeypatch):
    monkeypatch.setattr(smoke_check, "get_json", lambda url, timeout=90: (404, None))
    failures = []

    smoke_check.check_deep_health("https://api", require=False, failures=failures)

    assert failures == []


def test_check_deep_health_succeeds_on_first_attempt(monkeypatch):
    monkeypatch.setattr(
        smoke_check, "get_json",
        lambda url, timeout=90: (200, {"ok": True, "checks": {"supabase": "ok"}}),
    )
    failures = []

    smoke_check.check_deep_health("https://api", require=True, failures=failures)

    assert failures == []


def test_check_deep_health_retries_up_to_four_times_then_fails(monkeypatch):
    calls = {"n": 0}

    def always_503(url, timeout=90):
        calls["n"] += 1
        return 503, {"ok": False, "checks": {"supabase": "down"}}

    monkeypatch.setattr(smoke_check, "get_json", always_503)
    monkeypatch.setattr(smoke_check.time, "sleep", lambda s: None)
    failures = []

    smoke_check.check_deep_health("https://api", require=True, failures=failures)

    assert calls["n"] == 4
    assert len(failures) == 1
    assert "503" in failures[0]


# ── check_api() ──────────────────────────────────────────────────────────────

def test_check_api_fails_when_games_endpoint_does_not_return_a_list(monkeypatch):
    monkeypatch.setattr(smoke_check, "check_healthz", lambda *a, **k: None)
    monkeypatch.setattr(smoke_check, "check_deep_health", lambda *a, **k: None)

    def fake_get_json(url, timeout=90):
        if url.endswith("/games"):
            return 500, {"error": "db unreachable"}
        return 200, {"email": {}}

    monkeypatch.setattr(smoke_check, "get_json", fake_get_json)
    failures = []

    smoke_check.check_api("https://api", expect_commit="", require_healthz=False, failures=failures)

    assert len(failures) == 1
    assert "games" in failures[0]


def test_check_api_fails_when_auth_providers_missing_email_key(monkeypatch):
    monkeypatch.setattr(smoke_check, "check_healthz", lambda *a, **k: None)
    monkeypatch.setattr(smoke_check, "check_deep_health", lambda *a, **k: None)

    def fake_get_json(url, timeout=90):
        if url.endswith("/games"):
            return 200, []
        return 200, {"google": {}}  # no "email" key

    monkeypatch.setattr(smoke_check, "get_json", fake_get_json)
    failures = []

    smoke_check.check_api("https://api", expect_commit="", require_healthz=False, failures=failures)

    assert len(failures) == 1
    assert "auth/providers" in failures[0]


def test_check_api_clean_when_all_four_subchecks_pass(monkeypatch):
    monkeypatch.setattr(smoke_check, "check_healthz", lambda *a, **k: None)
    monkeypatch.setattr(smoke_check, "check_deep_health", lambda *a, **k: None)

    def fake_get_json(url, timeout=90):
        if url.endswith("/games"):
            return 200, [{"id": 1}]
        return 200, {"email": {}}

    monkeypatch.setattr(smoke_check, "get_json", fake_get_json)
    failures = []

    smoke_check.check_api("https://api", expect_commit="", require_healthz=False, failures=failures)

    assert failures == []


# ── main() ───────────────────────────────────────────────────────────────────

def test_main_returns_zero_when_nothing_fails(monkeypatch):
    monkeypatch.setattr(smoke_check, "check_site", lambda site, failures: None)
    monkeypatch.setattr(smoke_check, "check_browser", lambda site, failures: None)
    monkeypatch.setattr(smoke_check, "check_api", lambda api, c, r, failures: None)
    monkeypatch.setattr(
        "sys.argv",
        ["smoke_check.py", "--site", "https://site", "--api", "https://api"],
    )

    assert smoke_check.main() == 0


def test_main_returns_one_and_lists_every_failure(monkeypatch, capsys):
    def fail_site(site, failures):
        failures.append("SITE broke")

    def fail_api(api, c, r, failures):
        failures.append("API broke")

    monkeypatch.setattr(smoke_check, "check_site", fail_site)
    monkeypatch.setattr(smoke_check, "check_browser", lambda site, failures: None)
    monkeypatch.setattr(smoke_check, "check_api", fail_api)
    monkeypatch.setattr(
        "sys.argv",
        ["smoke_check.py", "--site", "https://site", "--api", "https://api"],
    )

    result = smoke_check.main()

    assert result == 1
    out = capsys.readouterr().out
    assert "SITE broke" in out
    assert "API broke" in out


def test_main_skip_site_and_skip_api_flags_short_circuit_checks(monkeypatch):
    called = []
    monkeypatch.setattr(smoke_check, "check_site", lambda site, failures: called.append("site"))
    monkeypatch.setattr(smoke_check, "check_browser", lambda site, failures: called.append("browser"))
    monkeypatch.setattr(smoke_check, "check_api", lambda api, c, r, failures: called.append("api"))
    monkeypatch.setattr(
        "sys.argv",
        ["smoke_check.py", "--site", "https://site", "--api", "https://api",
         "--skip-site", "--skip-api"],
    )

    assert smoke_check.main() == 0
    assert called == []
