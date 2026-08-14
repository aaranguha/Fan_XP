"""
nfl_runner.py

Checks today's NFL schedule and launches nfl_run_game.py for each team
with a home game today. Mirrors daily_runner.py for NBA.

NFL season runs September–February.
Games are played Thursday nights, Sunday afternoons/nights, Monday nights.

Usage:
    python nfl_runner.py

Cron (via GitHub Actions — see .github/workflows/nfl.yml):
    Sundays    17:00 UTC (noon ET)
    Mondays    23:00 UTC (6 PM ET)
    Thursdays  23:00 UTC (6 PM ET)
"""

import os
import subprocess
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

from nfl_teams import NFL_TEAMS

load_dotenv()

PYTHON = sys.executable

# TM lists plenty of non-game NFL inventory (open practices, hospitality
# add-ons) that still matches classificationName=Football on game day —
# filter those out by name.
TM_NOISE_TERMS = ("training camp", "not a game ticket", "club pass", "hotel package", "parking", "suite")


def get_home_teams_tm(today: str) -> list[str]:
    """
    Find today's NFL home games via the Ticketmaster Discovery API instead of
    ESPN's scoreboard — ESPN's CDN blocks GitHub Actions' hosted-runner IP
    ranges (confirmed 403 on a live run), while TM's API is already core to
    the scraping pipeline and reachable from the same runners.

    One query for all of today's Football events (TM has no per-team bulk
    schedule endpoint, but classification+date covers every team at once).
    TM lists the home team first, but preseason events are often prefixed
    ("Preseason Game 1: Pittsburgh Steelers v Green Bay Packers"), so we take
    the *leftmost* team name found in the event name rather than assuming it
    starts the string — and require a second team name elsewhere in the name
    too, so a bare listing like "New York Jets" (no opponent) doesn't match.
    """
    tm_api_key = os.getenv("TICKETMASTER_API_KEY", "").strip()
    if not tm_api_key:
        raise RuntimeError("TICKETMASTER_API_KEY not set in .env")

    resp = requests.get(
        "https://app.ticketmaster.com/discovery/v2/events.json",
        params={
            "apikey":             tm_api_key,
            "classificationName": "Football",
            "countryCode":        "US",
            "localStartDateTime": f"{today}T00:00:00,{today}T23:59:59",
            "sort":               "date,asc",
            "size":               50,
        },
        timeout=15,
    )
    resp.raise_for_status()
    events = resp.json().get("_embedded", {}).get("events", [])

    slugs = []
    for event in events:
        name = event.get("name", "")
        name_lower = name.lower()
        if any(term in name_lower for term in TM_NOISE_TERMS):
            continue

        matches = []  # (index, slug)
        for slug, team in NFL_TEAMS.items():
            idx = name_lower.find(team["tm_keyword"].lower())
            if idx >= 0:
                matches.append((idx, slug))
        if len(matches) < 2:
            continue  # only one (or zero) team mentioned — not a real matchup

        matches.sort()
        home_slug = matches[0][1]
        print(f"  Home game: {name}  →  slug: {home_slug}")
        slugs.append(home_slug)
    return slugs


def launch_team(slug: str, today: str) -> tuple:
    log_dir  = f"data/nfl/{slug}"
    os.makedirs(log_dir, exist_ok=True)
    log_path = f"{log_dir}/game.log"

    log_file = open(log_path, "a")
    log_file.write(f"\n{'='*54}\n")
    log_file.write(f"  Run started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    log_file.write(f"{'='*54}\n")
    log_file.flush()

    proc = subprocess.Popen(
        [PYTHON, "nfl_run_game.py", slug, today],
        stdout=log_file,
        stderr=log_file,
    )
    return proc, log_file


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"[{today}] Checking today's NFL schedule...")

    try:
        slugs = get_home_teams_tm(today)
    except Exception as e:
        print(f"Error fetching NFL schedule: {e}")
        sys.exit(1)

    if not slugs:
        print("No NFL home games today.")
        return

    print(f"\nLaunching {len(slugs)} game runner(s)...\n")

    procs = []
    for i, slug in enumerate(slugs):
        if i > 0:
            time.sleep(5)
        proc, log_file = launch_team(slug, today)
        procs.append((slug, proc, log_file))
        print(f"  [{slug}] PID {proc.pid}  →  data/nfl/{slug}/game.log")

    print(f"\nAll {len(procs)} runner(s) started. Waiting for completion...\n")

    succeeded = []
    for slug, proc, log_file in procs:
        proc.wait()
        log_file.close()
        if proc.returncode == 0:
            succeeded.append(slug)
            print(f"  [{slug}] done")
        elif proc.returncode == 2:
            print(f"  [{slug}] FAILED (Bot Detection)")
        else:
            print(f"  [{slug}] FAILED (exit {proc.returncode})")
            log_path = f"data/nfl/{slug}/game.log"
            try:
                with open(log_path) as f:
                    tail = f.readlines()[-30:]
                print(f"    --- tail of {log_path} ---")
                for line in tail:
                    print(f"    {line.rstrip()}")
                print(f"    --- end tail ---")
            except OSError as e:
                print(f"    (could not read {log_path}: {e})")

    if succeeded:
        # Regenerate HTML story pages for ALL teams (reads from Supabase, so
        # any team with prior data will also get refreshed pages)
        print("\nRegenerating NFL story pages...")
        all_slugs = list(NFL_TEAMS.keys()) if len(succeeded) > 1 else succeeded
        for slug in all_slugs:
            try:
                subprocess.run([PYTHON, "generate_nfl_story.py", slug], check=True)
                print(f"  [{slug}] HTML regenerated")
            except Exception as e:
                print(f"  [{slug}] HTML generation failed: {e}")

        print(f"\nCommitting and pushing to GitHub...")
        date_str  = datetime.now().strftime("%Y-%m-%d")
        teams_str = ", ".join(succeeded)

        github_token = os.getenv("GITHUB_TOKEN", "").strip()
        if github_token:
            git_email = os.getenv("GIT_USER_EMAIL", "github-actions[bot]@users.noreply.github.com")
            git_name  = os.getenv("GIT_USER_NAME",  "github-actions[bot]")
            subprocess.run(["git", "config", "user.email", git_email], check=True)
            subprocess.run(["git", "config", "user.name",  git_name],  check=True)

        subprocess.run(["git", "add", "data/nfl/", "docs/"], check=True)
        subprocess.run(["git", "commit", "-m", f"NFL auto-update {date_str}: {teams_str}"], check=True)
        result = subprocess.run(["git", "push"])
        print("  Pushed." if result.returncode == 0 else "  git push failed.")

    print("\nAll done.")


if __name__ == "__main__":
    main()
