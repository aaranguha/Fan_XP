"""
smoke_test_runner.py

One-off health check for a self-hosted scraping runner: launches a real
browser and does a single live scrape against a real Ticketmaster event,
then reports what it found and exits. No sleep_until, no Supabase writes,
no CSV files under data/ -- this never touches production data or state,
it only proves the runner's own environment can reach Ticketmaster and
scrape successfully.

Usage:
    python smoke_test_runner.py <event_url> [--parallel N]

--parallel N launches N scrapes of the same page at once as separate
processes (like N teams hitting halftime together on a 1 PM Sunday) and
reports how many succeeded, to load-test fetch_listings.browser_slot().
"""

import os
import shutil
import subprocess
import sys
import time

import requests
from fetch_listings import scrape_listings, build_rows_from_embedded_offers, CHROME_PROFILE_BASE
from datetime import datetime, timezone


def check_espn_scoreboard():
    """Confirm this machine can reach ESPN's live scoreboard -- wait_for_halftime()
    already degrades gracefully to a fixed-time fallback if this is blocked, so
    this isn't fatal either way, but it affects whether the halftime scrape
    fires at the real Q2-under-2-min moment or just a fixed 70 min after kickoff."""
    try:
        today = datetime.now().strftime("%Y%m%d")
        resp = requests.get(
            f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={today}",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10,
        )
        print(f"[smoke test] ESPN scoreboard: HTTP {resp.status_code}")
        if resp.status_code == 200:
            events = resp.json().get("events", [])
            print(f"[smoke test] ESPN reachable -- {len(events)} events returned. Live halftime detection will work.")
        else:
            print(f"[smoke test] ESPN blocked (HTTP {resp.status_code}) -- halftime will use the 70-min fallback timer instead of live detection. Not fatal.")
    except Exception as e:
        print(f"[smoke test] ESPN check failed: {e} -- halftime will use the 70-min fallback timer instead. Not fatal.")


def run_parallel(url: str, n: int) -> None:
    """Launch n single scrapes at once, each in its own process and Chrome
    profile, and fail unless every one of them gets real rows."""
    print(f"[load test] launching {n} concurrent scrapes "
          f"(BROWSER_SLOTS={os.getenv('BROWSER_SLOTS', 'default')})")
    start = time.monotonic()
    procs = [
        (i, subprocess.Popen(
            [sys.executable, __file__, url, "--worker", str(i)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        ))
        for i in range(n)
    ]
    passed = 0
    for i, p in procs:
        out, _ = p.communicate()
        result = [l for l in out.splitlines() if l.startswith("[worker")]
        print(result[-1] if result else f"[worker {i}] no result (exit {p.returncode})")
        if p.returncode != 0:
            print("\n".join(out.splitlines()[-15:]))
        passed += p.returncode == 0
    print(f"[load test] {passed}/{n} scrapes succeeded in {time.monotonic() - start:.0f}s")
    for i in range(n):
        shutil.rmtree(os.path.join(CHROME_PROFILE_BASE, f"smoke_test_{i}"), ignore_errors=True)
    sys.exit(0 if passed == n else 1)


def run_worker(url: str, i: int) -> None:
    started = time.monotonic()
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        _, _, _, embedded_offers = scrape_listings(url, max_retries=1, team_slug=f"smoke_test_{i}")
    except Exception as e:
        print(f"[worker {i}] FAILED after {time.monotonic() - started:.0f}s: {e}")
        sys.exit(1)
    rows = build_rows_from_embedded_offers(embedded_offers, scraped_at)
    print(f"[worker {i}] {'ok' if rows else 'EMPTY'}: {len(rows)} rows in {time.monotonic() - started:.0f}s")
    sys.exit(0 if rows else 1)


def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: python smoke_test_runner.py <event_url> [--parallel N]")
        sys.exit(1)

    url = args[0]
    if "--worker" in args:
        run_worker(url, int(args[args.index("--worker") + 1]))
    n = int(args[args.index("--parallel") + 1]) if "--parallel" in args else 1
    if n > 1:
        run_parallel(url, n)
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    check_espn_scoreboard()

    print(f"[smoke test] Scraping {url}")
    try:
        facets, offer_price_map, places_facets, embedded_offers = scrape_listings(
            url, max_retries=1, team_slug="smoke_test"
        )
    except Exception as e:
        print(f"[smoke test] SCRAPE FAILED: {e}")
        sys.exit(1)

    rows = build_rows_from_embedded_offers(embedded_offers, scraped_at)
    priced = [r for r in rows if r.get("price_usd") is not None]

    print(f"[smoke test] facets={len(facets)} places_facets={len(places_facets)} embedded_offers={len(embedded_offers)}")
    print(f"[smoke test] rows built: {len(rows)}, priced: {len(priced)}")

    if not rows:
        print("[smoke test] FAIL: no rows built at all.")
        sys.exit(1)
    if len(priced) < len(rows):
        print(f"[smoke test] WARNING: {len(rows) - len(priced)} rows missing a price.")

    print("[smoke test] PASS: runner can reach Ticketmaster and scrape real listings.")
    for r in rows[:5]:
        print(f"  sample: {r}")


if __name__ == "__main__":
    main()
