#!/usr/bin/env python3
"""Live smoke check for the deployed Fan XP site (Vercel) and API (Render).

Used by .github/workflows/deploy-guard.yml after every push to main, and safe
to run by hand:

    python3 scripts/smoke_check.py --site https://fan-xp.vercel.app \
        --api https://fanxp-api.onrender.com [--expect-commit <sha>]

Exits 0 when everything passes, 1 with a list of failures otherwise.
Stdlib only; if Playwright is installed it also loads two pages in headless
Chromium and fails on any uncaught page error.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

# (path, marker string that must appear in the body). Markers were picked
# from the real files in docs/.
SITE_PAGES = [
    ("index.html", "Seat Intelligence"),
    ("nfl.html", "Steelers"),
    ("account.html", 'id="signed-in"'),
    ("buy.html", "fanxp-auth.js"),
    ("sell.html", "fanxp-auth.js"),
    ("nfl_steelers_seatmap.html", "gamepick"),
    ("fanxp-auth.js", "fanxp-auth.js"),
    ("fanxp-account.css", "--bg"),
]
# Pages loaded in a real browser to catch uncaught JS errors.
BROWSER_PAGES = ["account.html", "nfl_steelers_seatmap.html"]

# Render's free tier cold-starts in 30-60s, so give each request a lot of room.
API_TIMEOUT = 90
SITE_TIMEOUT = 30


def fetch(url, timeout):
    """Return (status, body_text); status 0 on connection failure/timeout."""
    req = urllib.request.Request(url, headers={"User-Agent": "fanxp-smoke-check"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # DNS, timeout, reset...
        return 0, "%s: %s" % (type(e).__name__, e)


def get_json(url, timeout=API_TIMEOUT):
    status, body = fetch(url, timeout)
    try:
        return status, json.loads(body)
    except ValueError:
        return status, None


def check_site(site, failures):
    for path, marker in SITE_PAGES:
        url = "%s/%s" % (site, path)
        # Vercel can lag a few seconds behind the deployment status; retry.
        for attempt in range(3):
            status, body = fetch(url, SITE_TIMEOUT)
            if status == 200 and marker in body:
                break
            time.sleep(5)
        if status != 200:
            failures.append("SITE %s: HTTP %s" % (url, status))
        elif marker not in body:
            failures.append("SITE %s: 200 but marker %r missing" % (url, marker))
        else:
            print("ok   %s" % url)


def check_healthz(api, expect_commit, require, failures):
    """Poll /healthz for up to ~10 min (cold start + deploy lag)."""
    deadline = time.time() + 600
    last = "no response"
    strict = bool(expect_commit) or require
    while True:
        status, data = get_json(api + "/healthz")
        if status == 404 and not strict:
            print("skip /healthz (404, not required)")
            return
        if status == 200 and isinstance(data, dict) and data.get("ok") is True:
            if not expect_commit or data.get("commit") == expect_commit:
                print("ok   /healthz commit=%s" % data.get("commit"))
                return
            last = "serving commit %s, expected %s" % (data.get("commit"), expect_commit)
        else:
            last = "HTTP %s body %s" % (status, json.dumps(data)[:400] if data is not None else "not JSON")
        if time.time() > deadline:
            failures.append("API /healthz: %s (gave up after 10 min)" % last)
            return
        print("wait /healthz: %s" % last)
        time.sleep(20)


def check_deep_health(api, require, failures):
    """/healthz?deep=1 also queries Supabase; 503 means the database is down."""
    for attempt in range(4):
        status, data = get_json(api + "/healthz?deep=1")
        if status == 404 and not require:
            print("skip /healthz?deep=1 (404, not required)")
            return
        if status == 200 and isinstance(data, dict) and data.get("ok") is True:
            print("ok   /healthz?deep=1 checks=%s" % json.dumps(data.get("checks")))
            return
        if attempt < 3:
            time.sleep(20)
    failures.append("API /healthz?deep=1: HTTP %s body %s" % (status, json.dumps(data)[:400] if data is not None else "not JSON"))


def check_api(api, expect_commit, require_healthz, failures):
    check_healthz(api, expect_commit, require_healthz, failures)
    check_deep_health(api, bool(expect_commit) or require_healthz, failures)

    status, data = get_json(api + "/api/nfl/steelers/games")
    if status == 200 and isinstance(data, list):
        print("ok   /api/nfl/steelers/games (%d games)" % len(data))
    else:
        failures.append("API /api/nfl/steelers/games: HTTP %s, expected a JSON list, got %r" % (status, data))

    status, data = get_json(api + "/api/auth/providers")
    if status == 200 and isinstance(data, dict) and "email" in data:
        print("ok   /api/auth/providers")
    else:
        failures.append("API /api/auth/providers: HTTP %s, expected JSON with key 'email', got %r" % (status, data))


def check_browser(site, failures):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("skip browser checks (playwright not installed)")
        return
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for path in BROWSER_PAGES:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                try:
                    page.goto("%s/%s" % (site, path), wait_until="networkidle", timeout=60000)
                    page.wait_for_timeout(2000)
                except Exception as e:
                    errors.append("navigation failed: %s" % e)
                if errors:
                    failures.append("BROWSER %s: uncaught page errors: %s" % (path, "; ".join(errors)[:600]))
                else:
                    print("ok   browser %s (no page errors)" % path)
                page.close()
            browser.close()
    except Exception as e:
        # A browser that cannot launch is an environment problem, not a broken deploy.
        print("skip browser checks (could not run Chromium: %s)" % e)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--site", required=True, help="e.g. https://fan-xp.vercel.app")
    ap.add_argument("--api", required=True, help="e.g. https://fanxp-api.onrender.com")
    ap.add_argument("--expect-commit", default="", help="git sha /healthz must report")
    ap.add_argument("--require-healthz", action="store_true", help="fail if /healthz is missing")
    ap.add_argument("--skip-site", action="store_true", help="skip static site checks")
    ap.add_argument("--skip-api", action="store_true", help="skip API checks")
    a = ap.parse_args()
    site, api = a.site.rstrip("/"), a.api.rstrip("/")

    failures = []
    if not a.skip_site:
        check_site(site, failures)
        check_browser(site, failures)
    if not a.skip_api:
        check_api(api, a.expect_commit, a.require_healthz, failures)

    if failures:
        print("\nSMOKE CHECK FAILED (%d):" % len(failures))
        for f in failures:
            print(" - " + f)
        return 1
    print("\nSmoke check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
