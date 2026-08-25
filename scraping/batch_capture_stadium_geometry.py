"""
batch_capture_stadium_geometry.py

Finds each NFL team's next real home game on Ticketmaster, then runs
fetch_geometry.py against it to capture real per-seat geometry — the same
process already done manually for the 49ers. Skips any team that already
has data/{slug}/seatmap_geo.json.

This needs to run somewhere with the authenticated Ticketmaster session
(~/.tm_chrome_profile/shared) — i.e. the same machine that runs the actual
scrapers. Each team takes about a minute (opens a real, visible browser
window), so this can take 30+ minutes for the full list.

Usage:
    python3 batch_capture_stadium_geometry.py            # all 32 teams
    python3 batch_capture_stadium_geometry.py 49ers cowboys   # just these
"""

import os
import subprocess
import sys
import time

import requests
from dotenv import load_dotenv

from nfl_teams import NFL_TEAMS

load_dotenv()

PYTHON = sys.executable
TM_API_KEY = os.getenv("TICKETMASTER_API_KEY", "").strip()


def find_home_event_url(tm_keyword: str, expected_city: str) -> str | None:
    """
    Find this team's next real home game on Ticketmaster.

    A keyword search returns both home AND away appearances (and some non-
    game inventory), so we require the team's name to be the LEFTMOST team
    mention in the event name — same rule proven in nfl_runner.py's
    get_home_teams_tm, since TM lists the home team first (even with a
    "Preseason Game N:" prefix, it's still the first team name in the string).

    That alone isn't enough, though — NFL international games (London,
    Melbourne, etc.) are also listed with the "home" team first for
    scheduling purposes, but aren't played at that team's actual stadium.
    So we also require the event's venue city to match the team's real
    home city, otherwise geometry capture would grab the wrong venue.
    """
    resp = requests.get(
        "https://app.ticketmaster.com/discovery/v2/events.json",
        params={
            "apikey": TM_API_KEY,
            "keyword": tm_keyword,
            "classificationName": "Football",
            "sort": "date,asc",
            "size": 20,
        },
        timeout=15,
    )
    resp.raise_for_status()
    events = resp.json().get("_embedded", {}).get("events", [])

    noise = ("training camp", "not a game ticket", "club pass", "hotel package", "parking", "suite")

    for event in events:
        name = event.get("name", "")
        name_lower = name.lower()
        if any(term in name_lower for term in noise):
            continue

        matches = []
        for slug, team in NFL_TEAMS.items():
            idx = name_lower.find(team["tm_keyword"].lower())
            if idx >= 0:
                matches.append((idx, team["tm_keyword"]))
        if len(matches) < 2:
            continue
        matches.sort()
        home_keyword = matches[0][1]
        if home_keyword.lower() != tm_keyword.lower():
            continue  # this team isn't the home team in this event

        venues = event.get("_embedded", {}).get("venues", [{}])
        venue_city = venues[0].get("city", {}).get("name", "").lower()
        expected = expected_city.lower()
        # Substring match, not exact — TM's venue city ("Miami") sometimes
        # differs slightly from our team config's city ("Miami Gardens").
        if expected not in venue_city and venue_city not in expected:
            continue  # neutral-site / international game, not their real stadium

        url = event.get("url")
        if url and "ticketmaster.com" in url:
            return url
    return None


def main():
    requested = sys.argv[1:]
    slugs = requested if requested else list(NFL_TEAMS.keys())

    print(f"Capturing geometry for {len(slugs)} team(s)...\n")
    results = {"captured": [], "skipped": [], "failed": []}

    for slug in slugs:
        if slug not in NFL_TEAMS:
            print(f"  [{slug}] unknown team slug, skipping")
            results["failed"].append(slug)
            continue

        geo_path = f"data/{slug}/seatmap_geo.json"
        if os.path.isfile(geo_path):
            print(f"  [{slug}] already captured, skipping")
            results["skipped"].append(slug)
            continue

        team = NFL_TEAMS[slug]
        print(f"  [{slug}] looking up next home game...")
        time.sleep(2)  # defensive spacing against TM's Discovery API rate limit
        try:
            url = find_home_event_url(team["tm_keyword"], team["city"])
        except Exception as e:
            print(f"  [{slug}] TM lookup failed: {e}")
            results["failed"].append(slug)
            continue

        if not url:
            print(f"  [{slug}] no upcoming home game found on Ticketmaster — skipping")
            results["failed"].append(slug)
            continue

        print(f"  [{slug}] found: {url}")
        print(f"  [{slug}] capturing geometry (opens a real browser window)...")
        proc = subprocess.run([PYTHON, "fetch_geometry.py", url, slug])
        if proc.returncode == 0 and os.path.isfile(geo_path):
            print(f"  [{slug}] done\n")
            results["captured"].append(slug)
        else:
            print(f"  [{slug}] capture failed (exit {proc.returncode})\n")
            results["failed"].append(slug)

    print("=" * 54)
    print(f"  Captured: {len(results['captured'])}  {results['captured']}")
    print(f"  Skipped (already had data): {len(results['skipped'])}  {results['skipped']}")
    print(f"  Failed: {len(results['failed'])}  {results['failed']}")
    print("=" * 54)


if __name__ == "__main__":
    main()
