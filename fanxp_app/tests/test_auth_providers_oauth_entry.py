"""
Tests for fanxp_accounts.py's Google sign-in entry points:

  - auth_providers() (`GET /api/auth/providers`) — tells account.html whether
    "Continue with Google" should even be shown, by asking Supabase whether
    the Google provider is switched on. Result is cached 5 minutes so every
    page load doesn't hit Supabase's settings endpoint. Zero test coverage
    existed for this function before this file: a cache bug (never
    refreshing, or refreshing every call and defeating the point of caching)
    or a bad fallback (raising instead of defaulting to "off") would only
    ever surface live on account.html.

  - google_start() (`GET /api/auth/google?redirect_to=`) — the open-redirect
    guard in front of "Continue with Google". It only had positive-path and
    bad-host coverage before this file (test_accounts.py); the scheme check
    and the missing-parameter case were never exercised, and neither was the
    other allowed host ("localhost", used for local dev).
"""

from types import SimpleNamespace

import pytest

import fanxp_accounts
import fanxp_api


def call(method, path, token=None, json=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    client = fanxp_api.app.test_client()
    resp = client.open(path, method=method, json=json, headers=headers)
    return resp.status_code, resp.get_json()


@pytest.fixture(autouse=True)
def reset_providers_cache():
    # _providers_cache is a module-level dict mutated in place by
    # auth_providers(); without resetting it, whichever test runs first
    # would decide the cached value for every test that runs after it.
    original = dict(fanxp_accounts._providers_cache)
    fanxp_accounts._providers_cache["at"] = 0.0
    fanxp_accounts._providers_cache["value"] = None
    yield
    fanxp_accounts._providers_cache.clear()
    fanxp_accounts._providers_cache.update(original)


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


# ── auth_providers() ─────────────────────────────────────────────────────────

def test_google_enabled_in_supabase_settings_is_reported_on(monkeypatch):
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **k: FakeResponse({"external": {"google": True}}),
    )

    code, body = call("GET", "/api/auth/providers")

    assert code == 200
    assert body == {"google": True, "email": True}


def test_google_disabled_in_supabase_settings_is_reported_off(monkeypatch):
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **k: FakeResponse({"external": {"google": False}}),
    )

    code, body = call("GET", "/api/auth/providers")

    assert body == {"google": False, "email": True}


def test_missing_external_key_defaults_google_to_off(monkeypatch):
    # bool(r.json().get("external", {}).get("google")) must not raise when
    # Supabase's settings payload doesn't even have an "external" section.
    monkeypatch.setattr("requests.get", lambda *a, **k: FakeResponse({}))

    code, body = call("GET", "/api/auth/providers")

    assert code == 200
    assert body == {"google": False, "email": True}


def test_supabase_unreachable_falls_back_to_google_off_without_raising(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("no route to host")

    monkeypatch.setattr("requests.get", boom)

    code, body = call("GET", "/api/auth/providers")

    assert code == 200
    assert body == {"google": False, "email": True}


def test_malformed_json_response_falls_back_to_google_off_without_raising(monkeypatch):
    class BadResponse:
        def json(self):
            raise ValueError("not JSON")

    monkeypatch.setattr("requests.get", lambda *a, **k: BadResponse())

    code, body = call("GET", "/api/auth/providers")

    assert code == 200
    assert body == {"google": False, "email": True}


def test_second_call_within_five_minutes_reuses_cache_without_a_new_request(monkeypatch):
    calls = []

    def fake_get(*a, **k):
        calls.append(1)
        return FakeResponse({"external": {"google": True}})

    monkeypatch.setattr("requests.get", fake_get)

    first = call("GET", "/api/auth/providers")[1]
    second = call("GET", "/api/auth/providers")[1]

    assert first == second == {"google": True, "email": True}
    assert len(calls) == 1


def test_cache_expires_after_five_minutes_and_refetches(monkeypatch):
    responses = iter([
        FakeResponse({"external": {"google": False}}),
        FakeResponse({"external": {"google": True}}),
    ])
    monkeypatch.setattr("requests.get", lambda *a, **k: next(responses))

    clock = {"now": 1_000_000.0}
    monkeypatch.setattr("time.time", lambda: clock["now"])

    first = call("GET", "/api/auth/providers")[1]
    assert first == {"google": False, "email": True}

    clock["now"] += 301  # just past the 300s cache window
    second = call("GET", "/api/auth/providers")[1]

    assert second == {"google": True, "email": True}


def test_cache_not_yet_expired_at_exactly_the_boundary_keeps_old_value(monkeypatch):
    # The refresh condition is a strict "> 300", so a call at exactly 300s
    # must still serve the cached value, not refetch.
    responses = iter([
        FakeResponse({"external": {"google": False}}),
        FakeResponse({"external": {"google": True}}),
    ])
    monkeypatch.setattr("requests.get", lambda *a, **k: next(responses))

    clock = {"now": 1_000_000.0}
    monkeypatch.setattr("time.time", lambda: clock["now"])

    first = call("GET", "/api/auth/providers")[1]
    assert first == {"google": False, "email": True}

    clock["now"] += 300  # exactly at the boundary, not past it
    second = call("GET", "/api/auth/providers")[1]

    assert second == {"google": False, "email": True}


def test_requests_get_is_sent_the_supabase_service_key_as_apikey_header(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        return FakeResponse({"external": {"google": False}})

    monkeypatch.setattr("requests.get", fake_get)
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co/")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key-123")

    call("GET", "/api/auth/providers")

    # Trailing slash on SUPABASE_URL must not produce a double slash before
    # /auth/v1/settings.
    assert seen["url"] == "https://proj.supabase.co/auth/v1/settings"
    assert seen["headers"] == {"apikey": "service-key-123"}


# ── google_start() open-redirect guard ───────────────────────────────────────

def test_missing_redirect_to_is_rejected(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")

    code, body = call("GET", "/api/auth/google")

    assert code == 400
    assert "redirect_to" in body["error"]


def test_non_http_scheme_is_rejected(monkeypatch):
    # A javascript:/data: URI (or any non-http(s) scheme) must never reach
    # the redirect() call, even if the rest of the string looks like one of
    # our allowed hosts/paths.
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")

    code, body = call(
        "GET",
        "/api/auth/google?redirect_to=javascript:alert(1)//fan-xp.vercel.app/account.html",
    )

    assert code == 400


def test_localhost_is_an_allowed_dev_host(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")

    ok = call("GET", "/api/auth/google?redirect_to=http%3A%2F%2Flocalhost%3A5173%2Faccount.html")

    assert ok[0] == 302


def test_loopback_ip_is_an_allowed_dev_host(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")

    ok = call("GET", "/api/auth/google?redirect_to=http%3A%2F%2F127.0.0.1%3A5173%2Faccount.html")

    assert ok[0] == 302
